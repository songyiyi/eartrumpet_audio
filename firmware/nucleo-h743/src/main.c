/* 阶段 0 采集固件 —— NUCLEO-H743ZI2
 *
 * 采集骨导 + 气导两路 PDM，经 DFSDM 解码后通过 ST-Link 虚拟串口送到电脑。
 * 电脑侧由 tools/capture.py 存成 WAV，再进调参界面。
 *
 * 刻意**不在板上做 DSP**：阶段 0 要的是原始素材，处理放在电脑上，这样改
 * 参数不用重新烧录。DSP 核心（firmware/core）留到第二阶段上机。
 *
 * 带宽账：48 kHz × 2 通道 × 24 bit = 2.88 Mbit/s，所以串口跑 4 Mbaud
 * （STLINK-V3E 支持）。若链路跟不上，固件会累计丢块计数并在诊断里报出来
 * —— 让失败可见，而不是悄悄丢数据。
 */

#include <stdint.h>
#include <string.h>

#include "stm32h7xx_hal.h"

#include "board.h"

/* ---- 外部定义 ---------------------------------------------------------- */
void board_clock_init(void);
void board_dfsdm_init(void);
void board_dfsdm_start(void);

extern DFSDM_Filter_HandleTypeDef hdfsdm_flt0;
extern DFSDM_Filter_HandleTypeDef hdfsdm_flt1;
extern int32_t dfsdm_buf0[BOARD_BLOCK_SAMPLES * 2];
extern int32_t dfsdm_buf1[BOARD_BLOCK_SAMPLES * 2];

/* ---- 状态 -------------------------------------------------------------- */
static UART_HandleTypeDef huart;
static DMA_HandleTypeDef  hdma_uart_tx;

/* 每通道半块的样本数 */
#define HALF BOARD_BLOCK_SAMPLES

/* 发送帧：magic(2) + seq(2) + 每样本 3 字节 × 2 通道 */
#define FRAME_BYTES (4u + HALF * 6u)
static uint8_t  tx_frame[2][FRAME_BYTES];
static volatile uint8_t tx_busy;
static volatile uint16_t seq;
static volatile uint32_t dropped;      /* 串口跟不上而丢弃的块数 */

/* 两路 DMA 各自的半满标志，凑齐一对才组帧 —— 保证左右严格对应 */
static volatile uint8_t ready0, ready1;
static volatile uint8_t half0, half1;

void Error_Handler(void)
{
    __disable_irq();
    for (;;) {
        /* 红灯常亮表示初始化失败 */
        HAL_GPIO_WritePin(BOARD_LED_RED_PORT, BOARD_LED_RED_PIN, GPIO_PIN_SET);
    }
}

/* ---- 串口 -------------------------------------------------------------- */

static void uart_init(void)
{
    GPIO_InitTypeDef gpio = {0};

    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_USART3_CLK_ENABLE();
    __HAL_RCC_DMA2_CLK_ENABLE();

    /* PD8 = USART3_TX，PD9 = USART3_RX，Nucleo-144 上固定连到 ST-Link VCP */
    gpio.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = GPIO_AF7_USART3;
    HAL_GPIO_Init(GPIOD, &gpio);

    huart.Instance = BOARD_VCP_USART;
    huart.Init.BaudRate = BOARD_VCP_BAUD;
    huart.Init.WordLength = UART_WORDLENGTH_8B;
    huart.Init.StopBits = UART_STOPBITS_1;
    huart.Init.Parity = UART_PARITY_NONE;
    huart.Init.Mode = UART_MODE_TX_RX;
    huart.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart.Init.OverSampling = UART_OVERSAMPLING_8;  /* 高波特率需要 8 倍过采样 */
    if (HAL_UART_Init(&huart) != HAL_OK) {
        Error_Handler();
    }

    hdma_uart_tx.Instance = DMA2_Stream0;
    hdma_uart_tx.Init.Request = DMA_REQUEST_USART3_TX;
    hdma_uart_tx.Init.Direction = DMA_MEMORY_TO_PERIPH;
    hdma_uart_tx.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_uart_tx.Init.MemInc = DMA_MINC_ENABLE;
    hdma_uart_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    hdma_uart_tx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
    hdma_uart_tx.Init.Mode = DMA_NORMAL;
    hdma_uart_tx.Init.Priority = DMA_PRIORITY_MEDIUM;
    hdma_uart_tx.Init.FIFOMode = DMA_FIFOMODE_DISABLE;
    if (HAL_DMA_Init(&hdma_uart_tx) != HAL_OK) {
        Error_Handler();
    }
    __HAL_LINKDMA(&huart, hdmatx, hdma_uart_tx);

    HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
    HAL_NVIC_SetPriority(USART3_IRQn, 6, 1);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}

void DMA2_Stream0_IRQHandler(void) { HAL_DMA_IRQHandler(&hdma_uart_tx); }
void USART3_IRQHandler(void)       { HAL_UART_IRQHandler(&huart); }

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *h)
{
    (void)h;
    tx_busy = 0;
}

/* ---- 采集回调 ---------------------------------------------------------- */

/* DFSDM 数据寄存器高 24 位是结果、低 8 位是状态，因此**对有符号类型**
 * 右移 8 位取值；用无符号右移会把负样本变成巨大的正数。 */
static inline int32_t sample_of(int32_t raw) { return raw >> 8; }

static void try_emit(void)
{
    if (!ready0 || !ready1) {
        return;                     /* 等两路都齐 */
    }
    ready0 = ready1 = 0;

    if (tx_busy) {
        ++dropped;                  /* 串口没跟上，丢这一块并计数 */
        return;
    }

    const int32_t *s0 = &dfsdm_buf0[half0 ? HALF : 0];
    const int32_t *s1 = &dfsdm_buf1[half1 ? HALF : 0];
    uint8_t *p = tx_frame[seq & 1u];

    p[0] = 'S';
    p[1] = 'V';
    p[2] = (uint8_t)(seq & 0xFFu);
    p[3] = (uint8_t)(seq >> 8);
    uint8_t *d = p + 4;

    for (uint32_t i = 0; i < HALF; ++i) {
        const int32_t a = sample_of(s0[i]);
        const int32_t b = sample_of(s1[i]);

        *d++ = (uint8_t)(a & 0xFF);
        *d++ = (uint8_t)((a >> 8) & 0xFF);
        *d++ = (uint8_t)((a >> 16) & 0xFF);
        *d++ = (uint8_t)(b & 0xFF);
        *d++ = (uint8_t)((b >> 8) & 0xFF);
        *d++ = (uint8_t)((b >> 16) & 0xFF);
    }
    ++seq;

    tx_busy = 1;
    if (HAL_UART_Transmit_DMA(&huart, p, FRAME_BYTES) != HAL_OK) {
        tx_busy = 0;
        ++dropped;
    }
}

void HAL_DFSDM_FilterRegConvHalfCpltCallback(DFSDM_Filter_HandleTypeDef *h)
{
    if (h == &hdfsdm_flt0) { half0 = 0; ready0 = 1; }
    else                   { half1 = 0; ready1 = 1; }
    try_emit();
}

void HAL_DFSDM_FilterRegConvCpltCallback(DFSDM_Filter_HandleTypeDef *h)
{
    if (h == &hdfsdm_flt0) { half0 = 1; ready0 = 1; }
    else                   { half1 = 1; ready1 = 1; }
    try_emit();
}

/* ---- 主程序 ------------------------------------------------------------ */

static void leds_init(void)
{
    GPIO_InitTypeDef gpio = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();
    gpio.Pin = BOARD_LED_GREEN_PIN | BOARD_LED_RED_PIN;
    gpio.Mode = GPIO_MODE_OUTPUT_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &gpio);
}

int main(void)
{
    HAL_Init();
    board_clock_init();
    leds_init();
    uart_init();
    board_dfsdm_init();
    board_dfsdm_start();

    uint32_t next = HAL_GetTick() + 1000u;

    for (;;) {
        if ((int32_t)(HAL_GetTick() - next) >= 0) {
            next += 1000u;

            /* 绿灯每秒闪一次 = 采集在跑。这是最基本的"活着"指示。 */
            HAL_GPIO_TogglePin(BOARD_LED_GREEN_PORT, BOARD_LED_GREEN_PIN);

            /* 红灯亮 = 曾经丢过块，说明串口带宽不够。
             *
             * 诊断刻意不走串口发文本 —— 那会打断二进制流。丢块由 PC 端
             * 从帧头序号的跳变检测（capture.py 会报出来），电平与 RMS 也
             * 由 PC 端直接从样本算：数据都在它手上，没必要让固件再算一遍。
             * 固件只负责一件事：把丢块这个事实用一盏灯说出来。 */
            HAL_GPIO_WritePin(BOARD_LED_RED_PORT, BOARD_LED_RED_PIN,
                              dropped ? GPIO_PIN_SET : GPIO_PIN_RESET);
        }
    }
}

/* DFSDM 双通道 PDM 采集。
 *
 * 两颗传感器并联在同一根 DATA 线上，各自的 SELECT 引脚接成一高一低，
 * 于是它们分占时钟的两个边沿。DFSDM 侧用相邻的两个通道读同一个 DATIN：
 *
 *   通道 3  取自身引脚（SAME_CHANNEL），   下降沿采样
 *   通道 2  取后一通道引脚（FOLLOWING），   上升沿采样
 *
 * "后一通道"即通道 3，所以两个通道读的是同一个物理引脚 PC7。这正是
 * 硬件级采样同步的来源：同一个时钟、同一根线，不存在通道间时间偏移。
 *
 * 哪个通道对应骨导、哪个对应气导，取决于两块板 SELECT 的实际接法。
 * **不要靠推理判断** —— 上电后敲一下骨导传感器，看哪个通道的 RMS 跳。
 */

#include "stm32h7xx_hal.h"

#include "board.h"

void Error_Handler(void);

DFSDM_Channel_HandleTypeDef hdfsdm_ch2;   /* 上升沿 */
DFSDM_Channel_HandleTypeDef hdfsdm_ch3;   /* 下降沿 */
DFSDM_Filter_HandleTypeDef  hdfsdm_flt0;  /* 绑定通道 3 */
DFSDM_Filter_HandleTypeDef  hdfsdm_flt1;  /* 绑定通道 2 */

static DMA_HandleTypeDef hdma_flt0;
static DMA_HandleTypeDef hdma_flt1;

/* DMA 目标缓冲：双缓冲（半满 + 全满中断），每通道 2×BLOCK 个样本。
 * DFSDM 输出是 24 位有符号，但寄存器与 DMA 传输按 32 位字进行，
 * 低 8 位是通道号等状态位 —— 取值时必须**对有符号类型右移 8 位**，
 * 用无符号右移会把负样本变成巨大的正数。 */
__attribute__((section(".dma_buffer"), aligned(32)))
int32_t dfsdm_buf0[BOARD_BLOCK_SAMPLES * 2];
__attribute__((section(".dma_buffer"), aligned(32)))
int32_t dfsdm_buf1[BOARD_BLOCK_SAMPLES * 2];

static void channel_init(DFSDM_Channel_HandleTypeDef *h,
                         DFSDM_Channel_TypeDef *instance,
                         uint32_t pins, uint32_t spi_clock)
{
    h->Instance = instance;
    h->Init.OutputClock.Activation = ENABLE;
    /* 内核时钟选 SYSCLK：H743 的 DFSDM 只有 D2PCLK1 与 SYS 两个来源，
     * 而 D2PCLK1（96 MHz）除不尽 3.072 MHz。详见 board.h。 */
    h->Init.OutputClock.Selection = DFSDM_CHANNEL_OUTPUT_CLOCK_SYSTEM;
    h->Init.OutputClock.Divider = BOARD_DFSDM_CKOUTDIV;   /* 384/125 = 3.072 MHz */

    h->Init.Input.Multiplexer = DFSDM_CHANNEL_EXTERNAL_INPUTS;
    h->Init.Input.DataPacking = DFSDM_CHANNEL_STANDARD_MODE;
    h->Init.Input.Pins = pins;

    h->Init.SerialInterface.Type = DFSDM_CHANNEL_SPI_RISING;
    h->Init.SerialInterface.SpiClock = spi_clock;

    h->Init.Awd.FilterOrder = DFSDM_CHANNEL_FASTSINC_ORDER;
    h->Init.Awd.Oversampling = 1;
    h->Init.Offset = 0;

    /* sinc⁴ + 64 倍抽取的直流增益是 64⁴ = 2²⁴，而输出寄存器是 24 位
     * **有符号**（±2²³）。右移 1 位保证理论满量程也不会溢出，同时把
     * 分辨率损失降到最小。
     *
     * 实测若发现电平过低，可减小此值；出现削顶则增大。传感器实际电平
     * 远低于理论满量程，这里取保守值。 */
    h->Init.RightBitShift = 1;

    if (HAL_DFSDM_ChannelInit(h) != HAL_OK) {
        Error_Handler();
    }
}

static void filter_init(DFSDM_Filter_HandleTypeDef *h,
                        DFSDM_Filter_TypeDef *instance)
{
    h->Instance = instance;
    h->Init.RegularParam.Trigger = DFSDM_FILTER_SW_TRIGGER;
    h->Init.RegularParam.FastMode = ENABLE;
    h->Init.RegularParam.DmaMode = ENABLE;

    /* ST 建议音频用 3–5 阶。取 4 阶是阻带抑制与群延迟的折中：
     * 阶数越高抑制越好，但群延迟也越大（延迟预算见设计方案第 07 节）。 */
    h->Init.FilterParam.SincOrder = DFSDM_FILTER_SINC4_ORDER;
    h->Init.FilterParam.Oversampling = BOARD_DECIMATION;  /* 3.072M/64 = 48k */
    h->Init.FilterParam.IntOversampling = 1;

    if (HAL_DFSDM_FilterInit(h) != HAL_OK) {
        Error_Handler();
    }
}

static void dma_init(DMA_HandleTypeDef *hdma, DMA_Stream_TypeDef *stream,
                     uint32_t request, DFSDM_Filter_HandleTypeDef *flt)
{
    hdma->Instance = stream;
    hdma->Init.Request = request;
    hdma->Init.Direction = DMA_PERIPH_TO_MEMORY;
    hdma->Init.PeriphInc = DMA_PINC_DISABLE;
    hdma->Init.MemInc = DMA_MINC_ENABLE;
    hdma->Init.PeriphDataAlignment = DMA_PDATAALIGN_WORD;
    hdma->Init.MemDataAlignment = DMA_MDATAALIGN_WORD;
    hdma->Init.Mode = DMA_CIRCULAR;
    hdma->Init.Priority = DMA_PRIORITY_HIGH;
    hdma->Init.FIFOMode = DMA_FIFOMODE_DISABLE;
    if (HAL_DMA_Init(hdma) != HAL_OK) {
        Error_Handler();
    }
    __HAL_LINKDMA(flt, hdmaReg, *hdma);
}

void board_dfsdm_init(void)
{
    GPIO_InitTypeDef gpio = {0};

    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_DFSDM1_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    /* PC2 = DFSDM1_CKOUT，PC7 = DFSDM1_DATIN3。
     * 时钟线要求上升/下降时间 ≤13 ns，所以用最高速度档。 */
    gpio.Pin = BOARD_DFSDM_CKOUT_PIN | BOARD_DFSDM_DATIN_PIN;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = BOARD_DFSDM_AF;
    HAL_GPIO_Init(GPIOC, &gpio);

    /* 通道 3：读自身的 DATIN3 引脚，内部时钟二分频、下降沿采样 */
    channel_init(&hdfsdm_ch3, DFSDM1_Channel3,
                 DFSDM_CHANNEL_SAME_CHANNEL_PINS,
                 DFSDM_CHANNEL_SPI_CLOCK_INTERNAL_DIV2_FALLING);

    /* 通道 2：读"后一通道"的引脚（即通道 3 的 DATIN3），上升沿采样。
     * 两个通道因此共用同一根物理数据线，各占一个时钟边沿。 */
    channel_init(&hdfsdm_ch2, DFSDM1_Channel2,
                 DFSDM_CHANNEL_FOLLOWING_CHANNEL_PINS,
                 DFSDM_CHANNEL_SPI_CLOCK_INTERNAL_DIV2_RISING);

    filter_init(&hdfsdm_flt0, DFSDM1_Filter0);
    filter_init(&hdfsdm_flt1, DFSDM1_Filter1);

    dma_init(&hdma_flt0, DMA1_Stream0, DMA_REQUEST_DFSDM1_FLT0, &hdfsdm_flt0);
    dma_init(&hdma_flt1, DMA1_Stream1, DMA_REQUEST_DFSDM1_FLT1, &hdfsdm_flt1);

    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
    HAL_NVIC_SetPriority(DMA1_Stream1_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream1_IRQn);

    if (HAL_DFSDM_FilterConfigRegChannel(&hdfsdm_flt0, DFSDM_CHANNEL_3,
                                         DFSDM_CONTINUOUS_CONV_ON) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_DFSDM_FilterConfigRegChannel(&hdfsdm_flt1, DFSDM_CHANNEL_2,
                                         DFSDM_CONTINUOUS_CONV_ON) != HAL_OK) {
        Error_Handler();
    }
}

void board_dfsdm_start(void)
{
    /* 先启从通道再启主通道，避免两路起始相位不一致 */
    if (HAL_DFSDM_FilterRegularStart_DMA(&hdfsdm_flt1, dfsdm_buf1,
                                         BOARD_BLOCK_SAMPLES * 2) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_DFSDM_FilterRegularStart_DMA(&hdfsdm_flt0, dfsdm_buf0,
                                         BOARD_BLOCK_SAMPLES * 2) != HAL_OK) {
        Error_Handler();
    }
}

void DMA1_Stream0_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_flt0);
}

void DMA1_Stream1_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_flt1);
}

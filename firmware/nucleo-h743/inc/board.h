/* 板级定义 —— NUCLEO-H743ZI2 上的采集固件。
 *
 * 用途：阶段 0 实验。采集骨导（V2S200D）与气导（IM69D130）两路 PDM，
 * 经 DFSDM 解码后通过 ST-Link 虚拟串口送到电脑，由 tools/capture.py 存成
 * WAV，再进调参界面。
 *
 * 这份固件**不做 DSP**：阶段 0 要的是原始素材，处理放在电脑上做，这样
 * 改参数不用重新烧录。DSP 核心（firmware/core）留到第二阶段上机。
 */

#ifndef BOARD_H
#define BOARD_H

/* ---------------------------------------------------------------- 时钟
 *
 * DFSDM 的时钟推导是这份固件里最关键、也最容易出错的部分。
 *
 * H743 的 DFSDM1 内核时钟**只有两个来源**：D2PCLK1 或 SYSCLK
 * （不像某些型号可以挂 PLL/SAI 时钟）。而 CKOUT = 内核时钟 / CKOUTDIV，
 * CKOUTDIV 取值 1..256。
 *
 * 要得到精确的 3.072 MHz：
 *
 *   走 D2PCLK1：SYSCLK 384 → HCLK 192 → PCLK1 96 MHz
 *               96 / 3.072 = 31.25  →  非整数，✗
 *
 *   走 SYSCLK ：384 MHz / 125 = 3,072,000 Hz  →  **零误差**，✓
 *
 * 所以选 SYSCLK 作内核时钟、CKOUTDIV = 125。
 *
 * 384 MHz 由 8 MHz HSE（Nucleo 上由 ST-Link 的 MCO 提供）产生：
 *   DIVM1 = 2  →  参考 4 MHz
 *   N     = 192 →  VCO = 768 MHz（宽范围 192–836 MHz 内）
 *   P     = 2  →  SYSCLK = 384 MHz
 *
 * 384 MHz 低于 VOS1 的 400 MHz 上限，因此不需要开 VOS0 超频档，
 * 电源配置更简单也更稳。
 */
#define BOARD_HSE_HZ        8000000U
#define BOARD_SYSCLK_HZ     384000000U
#define BOARD_DFSDM_CKOUTDIV 125U           /* 384 MHz / 125 = 3.072 MHz */
#define BOARD_PDM_CLK_HZ    3072000U

/* 音频参数：3.072 MHz / 64 = 48 kHz */
#define BOARD_DECIMATION    64U
#define BOARD_SAMPLE_RATE   (BOARD_PDM_CLK_HZ / BOARD_DECIMATION)

/* ---------------------------------------------------------------- 引脚
 *
 * 两颗传感器并联挂在同一根 DATA 线上，靠各自 SELECT 引脚占用时钟的
 * 不同边沿。DFSDM 侧用相邻的两个通道读同一个 DATIN 引脚：
 *
 *   通道 3：取自身引脚（SAME_CHANNEL），下降沿采样
 *   通道 2：取后一通道的引脚（FOLLOWING_CHANNEL，即通道 3 的 DATIN），上升沿采样
 *
 * 这就是"硬件级采样同步"的来源 —— 两路共用同一个时钟与数据线，
 * 不存在通道间时间偏移。
 */
#define BOARD_DFSDM_CKOUT_PORT  GPIOC
#define BOARD_DFSDM_CKOUT_PIN   GPIO_PIN_2   /* PC2  DFSDM1_CKOUT */
#define BOARD_DFSDM_DATIN_PORT  GPIOC
#define BOARD_DFSDM_DATIN_PIN   GPIO_PIN_7   /* PC7  DFSDM1_DATIN3 */

/* 两个引脚的复用编号**不同**，必须分别初始化。
 *
 * DFSDM1 在 AF3/AF4/AF6/AF11 上都有定义，具体用哪个取决于引脚 —— HAL 头
 * 文件只给编号不给映射，只能查数据手册的复用表。按表中 AF 升序排列，用
 * 已知锚点反推：
 *
 *   PC2：CDSLEEP(AF0) … SPI2_MISO(AF5) → DFSDM1_CKOUT → ULPI_DIR(AF10)
 *        夹在 AF5 与 AF10 之间，DFSDM 可选项里只有 AF6 符合
 *
 *   PC7：TIM3_CH2(AF2) → TIM8_CH2(AF3) → DFSDM1_DATIN3 → I2S3_MCK(AF6)
 *        紧跟 AF3 之后、AF6 之前，即 AF4
 *
 * 写错的症状是**完全没有信号**，而且看不出哪里错。 */
#define BOARD_DFSDM_CKOUT_AF    GPIO_AF6_DFSDM1
#define BOARD_DFSDM_DATIN_AF    GPIO_AF4_DFSDM1

/* ST-Link 虚拟串口（Nucleo-144 上固定为 USART3 / PD8 TX / PD9 RX） */
#define BOARD_VCP_USART         USART3
/* 带宽账：48000 × 2 通道 × 3 字节 = 288 kB/s。串口 8N1 每字节占 10 位，
 * 即需 2.88 Mbit/s。2 Mbaud 不够，必须 4 Mbaud 才有余量。
 *
 * STLINK-V3E（Nucleo-H743ZI2 板载）支持；更老的 ST-LINK/V2-1 未必。
 * 若丢块（板上红灯亮、capture.py 报序号跳变），退路是降到 2000000 并把
 * 发送改成 16 位——阶段 0 只是采素材，传感器本身也只有 64.5 dB 信噪比。
 *
 * 改这里必须同步改 tools/capture.py 的 --baud 默认值，否则收不到数据。 */
#define BOARD_VCP_BAUD          4000000U

/* 用户 LED（Nucleo-144） */
#define BOARD_LED_GREEN_PORT    GPIOB
#define BOARD_LED_GREEN_PIN     GPIO_PIN_0
#define BOARD_LED_RED_PORT      GPIOB
#define BOARD_LED_RED_PIN       GPIO_PIN_14

/* 每次 DMA 半传输回调处理的样本数（每通道） */
#define BOARD_BLOCK_SAMPLES     256U

#endif /* BOARD_H */

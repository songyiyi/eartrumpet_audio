/* 时钟树配置 —— 目标是让 DFSDM 输出精确的 3.072 MHz。
 *
 * 推导过程见 board.h 顶部注释。结论：SYSCLK = 384 MHz，DFSDM 内核时钟
 * 选 SYSCLK，CKOUTDIV = 125，得 3,072,000 Hz 零误差。
 */

#include "stm32h7xx_hal.h"

#include "board.h"

void Error_Handler(void);

void board_clock_init(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};
    RCC_PeriphCLKInitTypeDef periph = {0};

    /* 384 MHz 在 VOS1 的 400 MHz 上限之内，不需要 VOS0 超频档。
     * 用 VOS1 电源配置更简单，也少一处出错的地方。 */
    HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY);
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {
    }

    /* HSE：Nucleo-144 上由 ST-Link 的 MCO 提供 8 MHz 方波，
     * 因此是 BYPASS 模式而不是接晶振的 ON 模式 —— 这一条写错会卡在
     * 等待 HSE 就绪上，表现为程序完全跑不起来。 */
    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState = RCC_HSE_BYPASS;
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLM = 2;              /* 8 MHz / 2 = 4 MHz 参考           */
    osc.PLL.PLLN = 192;            /* 4 × 192 = 768 MHz VCO            */
    osc.PLL.PLLP = 2;              /* 768 / 2 = 384 MHz SYSCLK         */
    osc.PLL.PLLQ = 8;              /* 96 MHz，给需要的外设备用          */
    osc.PLL.PLLR = 2;
    osc.PLL.PLLRGE = RCC_PLL1VCIRANGE_2;   /* 参考落在 4–8 MHz 档      */
    osc.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;   /* VCO 768 MHz 属宽范围      */
    osc.PLL.PLLFRACN = 0;                  /* 整数分频即可精确命中       */
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        Error_Handler();
    }

    clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                    RCC_CLOCKTYPE_D1PCLK1 | RCC_CLOCKTYPE_PCLK1 |
                    RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_D3PCLK1;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.SYSCLKDivider = RCC_SYSCLK_DIV1;   /* 384 MHz                  */
    clk.AHBCLKDivider = RCC_HCLK_DIV2;     /* HCLK 192 MHz（上限 240） */
    clk.APB1CLKDivider = RCC_APB1_DIV2;    /* 96 MHz                   */
    clk.APB2CLKDivider = RCC_APB2_DIV2;    /* 96 MHz                   */
    clk.APB3CLKDivider = RCC_APB3_DIV2;
    clk.APB4CLKDivider = RCC_APB4_DIV2;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_4) != HAL_OK) {
        Error_Handler();
    }

    /* DFSDM 内核时钟选 SYSCLK。
     * H743 只有 D2PCLK1 与 SYS 两个选项，而 D2PCLK1（96 MHz）除不尽
     * 3.072 MHz，所以必须选 SYS。 */
    periph.PeriphClockSelection = RCC_PERIPHCLK_DFSDM1 | RCC_PERIPHCLK_USART3;
    periph.Dfsdm1ClockSelection = RCC_DFSDM1CLKSOURCE_SYS;
    periph.Usart234578ClockSelection = RCC_USART234578CLKSOURCE_D2PCLK1;
    if (HAL_RCCEx_PeriphCLKConfig(&periph) != HAL_OK) {
        Error_Handler();
    }
}

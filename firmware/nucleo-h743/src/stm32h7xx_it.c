/* 中断与异常处理。
 *
 * DMA 与串口的中断处理函数分别定义在 dfsdm.c 与 main.c 里（就近于它们
 * 操作的句柄），这里只放内核异常与 SysTick。
 */

#include "stm32h7xx_hal.h"

void NMI_Handler(void) { }

/* 三个硬故障处理刻意做成死循环而不是自动复位：
 * 复位会掩盖问题，让固件看起来"偶尔重启"；死循环则会让绿灯停止闪烁，
 * 一眼就能看出卡死了，再用调试器接上去看栈。 */
void HardFault_Handler(void)  { for (;;) { } }
void MemManage_Handler(void)  { for (;;) { } }
void BusFault_Handler(void)   { for (;;) { } }
void UsageFault_Handler(void) { for (;;) { } }

void SVC_Handler(void)     { }
void DebugMon_Handler(void) { }
void PendSV_Handler(void)  { }

void SysTick_Handler(void)
{
    HAL_IncTick();
}

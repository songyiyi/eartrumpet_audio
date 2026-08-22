/* STM32H743ZI 链接脚本 —— NUCLEO-H743ZI2
 *
 * H7 的内存布局有个必须注意的地方：**DMA1/DMA2 无法访问 DTCM RAM**
 * （0x20000000）。它们位于 D2 域，只能到达 D2 SRAM 与 D1 的 AXI SRAM。
 *
 * 如果把 DMA 缓冲放进 DTCM，采集会**静默失败** —— 不报错、没有数据，
 * 极难定位。所以这里：
 *   .data/.bss  → AXI SRAM (D1)，容量大且 DMA 可达
 *   .dma_buffer → D2 SRAM，专供 DFSDM 与串口的 DMA 使用
 *   DTCM        → 留给栈，CPU 访问零等待
 */

ENTRY(Reset_Handler)

_estack = ORIGIN(DTCMRAM) + LENGTH(DTCMRAM);

_Min_Heap_Size  = 0x200;
_Min_Stack_Size = 0x2000;

MEMORY
{
  FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 2048K
  DTCMRAM (xrw) : ORIGIN = 0x20000000, LENGTH = 128K   /* DMA 不可达 */
  RAM_D1  (xrw) : ORIGIN = 0x24000000, LENGTH = 512K   /* AXI SRAM   */
  RAM_D2  (xrw) : ORIGIN = 0x30000000, LENGTH = 288K   /* DMA 缓冲   */
  RAM_D3  (xrw) : ORIGIN = 0x38000000, LENGTH = 64K
}

SECTIONS
{
  .isr_vector : { . = ALIGN(4); KEEP(*(.isr_vector)) . = ALIGN(4); } >FLASH

  .text : {
    . = ALIGN(4);
    *(.text) *(.text*) *(.glue_7) *(.glue_7t) *(.eh_frame)
    KEEP(*(.init)) KEEP(*(.fini))
    . = ALIGN(4);
    _etext = .;
  } >FLASH

  .rodata : { . = ALIGN(4); *(.rodata) *(.rodata*) . = ALIGN(4); } >FLASH

  .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } >FLASH
  .ARM : { __exidx_start = .; *(.ARM.exidx*) __exidx_end = .; } >FLASH

  .preinit_array : { PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*)) PROVIDE_HIDDEN(__preinit_array_end = .); } >FLASH
  .init_array : { PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array*))
    PROVIDE_HIDDEN(__init_array_end = .); } >FLASH
  .fini_array : { PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array*))
    PROVIDE_HIDDEN(__fini_array_end = .); } >FLASH

  _sidata = LOADADDR(.data);

  .data : {
    . = ALIGN(4);
    _sdata = .;
    *(.data) *(.data*)
    . = ALIGN(4);
    _edata = .;
  } >RAM_D1 AT> FLASH

  .bss : {
    . = ALIGN(4);
    _sbss = .;
    __bss_start__ = _sbss;
    *(.bss) *(.bss*) *(COMMON)
    . = ALIGN(4);
    _ebss = .;
    __bss_end__ = _ebss;
  } >RAM_D1

  /* DMA 专用缓冲。放在 D2 SRAM，DMA1/DMA2 可达。
   * 32 字节对齐是为了配合 D-Cache 的行大小 —— 若日后开启 D-Cache，
   * 缓冲区必须按 cache line 对齐才能安全做 invalidate/clean。 */
  .dma_buffer (NOLOAD) : {
    . = ALIGN(32);
    *(.dma_buffer)
    . = ALIGN(32);
  } >RAM_D2

  ._user_heap_stack : {
    . = ALIGN(8);
    PROVIDE(end = .);
    PROVIDE(_end = .);
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
  } >DTCMRAM

  /DISCARD/ : { libc.a(*) libm.a(*) libgcc.a(*) }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

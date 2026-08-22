# 阶段 0 采集固件 —— NUCLEO-H743ZI2
#
# 不依赖 STM32CubeIDE，也不需要注册 ST 账号：HAL 与 CMSIS 从 ST 的官方
# GitHub 仓库拉取（见 firmware/vendor/fetch.sh）。
#
#   make            编译，产出 build/capture.elf / .bin / .hex
#   make size       查看 Flash 与 RAM 占用
#   make flash      拷贝 .bin 到 Nucleo 的 U 盘（最省事的烧录方式）
#   make clean

TARGET  := capture
BUILD   := build
VENDOR  := ../vendor

# ---- 工具链 -----------------------------------------------------------------
# 若 arm-none-eabi-gcc 不在 PATH 里，用 make TOOLCHAIN=/path/to/bin/ 覆盖
TOOLCHAIN ?=
CC      := $(TOOLCHAIN)arm-none-eabi-gcc
OBJCOPY := $(TOOLCHAIN)arm-none-eabi-objcopy
SIZE    := $(TOOLCHAIN)arm-none-eabi-size

# ---- 目标核心 ---------------------------------------------------------------
# Cortex-M7 带双精度 FPU（fpv5-d16）。硬浮点 ABI 是必须的：
# DSP 核心用 float 运算，软浮点会慢一个数量级。
CPU := -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard

DEFS := -DSTM32H743xx -DUSE_HAL_DRIVER

INCS := \
  -Iinc \
  -I$(VENDOR)/cmsis_core/CMSIS/Core/Include \
  -I$(VENDOR)/cmsis_device_h7/Include \
  -I$(VENDOR)/stm32h7xx_hal_driver/Inc

# -ffp-contract=off 与主机对拍测试保持一致。本固件虽然不跑 DSP，但
# firmware/core 将来会编进来，届时浮点行为必须与已验证的一致。
CFLAGS := $(CPU) $(DEFS) $(INCS) -std=c99 -O2 -g3 \
  -Wall -Wextra -ffp-contract=off \
  -ffunction-sections -fdata-sections -fno-common

LDSCRIPT := STM32H743ZITx_FLASH.ld
LDFLAGS  := $(CPU) -T$(LDSCRIPT) -specs=nano.specs -specs=nosys.specs \
  -Wl,--gc-sections -Wl,-Map=$(BUILD)/$(TARGET).map -lm

# ---- 源文件 -----------------------------------------------------------------
HAL_SRC := \
  stm32h7xx_hal.c stm32h7xx_hal_cortex.c stm32h7xx_hal_dma.c \
  stm32h7xx_hal_dma_ex.c stm32h7xx_hal_gpio.c stm32h7xx_hal_pwr.c \
  stm32h7xx_hal_pwr_ex.c stm32h7xx_hal_rcc.c stm32h7xx_hal_rcc_ex.c \
  stm32h7xx_hal_uart.c stm32h7xx_hal_uart_ex.c stm32h7xx_hal_dfsdm.c \
  stm32h7xx_hal_exti.c stm32h7xx_hal_hsem.c stm32h7xx_hal_mdma.c \
  stm32h7xx_hal_flash.c stm32h7xx_hal_flash_ex.c

C_SRC := \
  src/main.c src/clock.c src/dfsdm.c src/stm32h7xx_it.c \
  $(VENDOR)/cmsis_device_h7/Source/Templates/system_stm32h7xx.c \
  $(addprefix $(VENDOR)/stm32h7xx_hal_driver/Src/,$(HAL_SRC))

ASM_SRC := $(VENDOR)/cmsis_device_h7/Source/Templates/gcc/startup_stm32h743xx.s

OBJ := $(addprefix $(BUILD)/,$(notdir $(C_SRC:.c=.o))) \
       $(addprefix $(BUILD)/,$(notdir $(ASM_SRC:.s=.o)))
vpath %.c $(sort $(dir $(C_SRC)))
vpath %.s $(dir $(ASM_SRC))

# ---- 规则 -------------------------------------------------------------------
.PHONY: all size flash clean

all: $(BUILD)/$(TARGET).bin $(BUILD)/$(TARGET).hex size

$(BUILD):
	@mkdir -p $@

$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) -c $(CFLAGS) -MMD -MP -o $@ $<

$(BUILD)/%.o: %.s | $(BUILD)
	$(CC) -c $(CPU) -x assembler-with-cpp -o $@ $<

$(BUILD)/$(TARGET).elf: $(OBJ) $(LDSCRIPT)
	$(CC) $(OBJ) $(LDFLAGS) -o $@

$(BUILD)/$(TARGET).bin: $(BUILD)/$(TARGET).elf
	$(OBJCOPY) -O binary -S $< $@

$(BUILD)/$(TARGET).hex: $(BUILD)/$(TARGET).elf
	$(OBJCOPY) -O ihex $< $@

size: $(BUILD)/$(TARGET).elf
	@$(SIZE) $<

# Nucleo 插上 USB 后会挂载成一个 U 盘，把 .bin 拖进去即完成烧录。
# 修改 NUCLEO_DRIVE 为你系统上的实际盘符/挂载点。
NUCLEO_DRIVE ?= /d
flash: $(BUILD)/$(TARGET).bin
	@test -d $(NUCLEO_DRIVE) || { echo "找不到 $(NUCLEO_DRIVE)，用 make flash NUCLEO_DRIVE=/e 指定盘符"; exit 1; }
	cp $< $(NUCLEO_DRIVE)/
	@echo "已拷贝到 $(NUCLEO_DRIVE)，烧录会自动进行（板上 LD1 会闪烁）"

clean:
	rm -rf $(BUILD)

-include $(wildcard $(BUILD)/*.d)

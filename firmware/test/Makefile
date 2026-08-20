# 主机侧对拍测试。
#
# -ffp-contract=off 是**必须的**：默认情况下 GCC 会把 a*b+c 融合成 FMA 指令，
# 中间结果不做舍入。那样算出来更精确，但与 Python 的逐步舍入不再逐位一致，
# 对拍必然失败。同理禁用 fast-math 一类的重结合优化。

CC      ?= gcc
CFLAGS  := -std=c99 -O2 -Wall -Wextra -Wpedantic \
           -ffp-contract=off -fno-fast-math \
           -fno-unsafe-math-optimizations
LDFLAGS := -lm

CORE    := ../core
SRCS    := test_golden.c $(CORE)/sv_biquad.c $(CORE)/sv_design.c $(CORE)/sv_chain.c
TARGET  := test_golden

.PHONY: all run clean

all: $(TARGET)

$(TARGET): $(SRCS) $(CORE)/sv_biquad.h $(CORE)/sv_design.h $(CORE)/sv_chain.h
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

run: $(TARGET)
	./$(TARGET) golden.bin

clean:
	rm -f $(TARGET) $(TARGET).exe

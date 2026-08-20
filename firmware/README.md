# 固件

目标芯片 **STM32H750 / H743**。选型理由见 [设计方案](../docs/design.html) 第 05 节：
DFSDM 硬件解码 PDM 且输出 24 bit（ESP32-S3 的 PDM RX 只有 16 bit，被位深要求排除），
裸机可控的实时确定性满足演出可靠性要求。

## 目录结构

```
core/     平台无关 DSP —— 主机与 STM32 编译同一份代码，无 HAL 依赖
  sv_biquad.[ch]   双二阶级联，Direct Form I，CMSIS 系数约定
  sv_design.[ch]   RBJ Audio EQ Cookbook 系数设计
  sv_chain.[ch]    完整处理链 + 参数结构 + 三旋钮映射
test/     主机侧对拍测试
  test_golden.c    与 Python 参考实现逐位比对
stm32/    平台层（需要 HAL，无法在主机编译）
  sv_knobs.[ch]    ADC 读旋钮：平滑 + 死区
  sv_audio.[ch]    DFSDM ↔ float 格式转换与管线
```

## 逐位对拍验证

固件 C 实现与 Python 参考实现在相同输入下**每一个 float 都逐位相同**。

```bash
python tools/gen_golden.py && make -C firmware/test run
```

不设容差、按 IEEE-754 位模式比较。理由是：只要允许容差，运算次序错误、精度退化、
状态未跨块保持这类问题都可能藏在容差之下，等到真机上才以杂音或周期性咔哒暴露出来。

> **编译必须带 `-ffp-contract=off`。** 否则编译器会把 `a*b+c` 融合成 FMA 指令，
> 结果更精确但与 Python 的逐步舍入不再逐位一致，对拍必然失败。

这套对拍已经抓到两个真实缺陷，都是肉眼审查不易发现的：

1. `sv_chain_process` 曾对超长输入**静默截断**（`if (n > SV_MAX_BLOCK) n = SV_MAX_BLOCK`），
   表现为全零输入在第 128 个样本后出现非零输出。现改为内部分块循环，对外无块长上限。
2. 气导路搁架 Q 值 C 写 `0.707f`、Python 写 `0.707`，二者数值不同，造成约 1e-7 的
   系数偏差。差异恰好从延迟长度之后的样本开始出现，据此定位。

## CubeMX 需要配置什么

`core/` 与 `stm32/` 里的代码不含初始化，以下外设需由 CubeMX 生成 `MX_*_Init()`：

### DFSDM —— 双通道 PDM 输入

两颗传感器挂**同一根数据线**，靠时钟极性区分左右，因此在硬件层面天然采样同步。

| 项 | 设置 |
| --- | --- |
| 通道 | 两个通道共用同一 DATIN 引脚 |
| 时钟极性 | 一个取上升沿、另一个取下降沿 |
| 输出时钟 | 3.072 MHz（= 48 kHz × 64） |
| 滤波器 | Sinc³ 或 Sinc⁴，抽取率 64 |
| 分辨率 | 24 bit（DFSDM 原生） |
| DMA | 双缓冲，半满/全满中断，每次 `SV_BLOCK_SAMPLES` 个样本 |

> PDM 时钟**必须跑满 3.072 MHz**。IM69D130 的省电模式是靠降时钟实现的
> （980 µA → 300 µA），降下去会直接牺牲 69 dB(A) 的信噪比指标。

### SAI —— I²S 输出至 PCM5102A

主模式，24 bit 帧，48 kHz，DMA 循环双缓冲。

> PCM5102A 的 **FLT 引脚要拉到低延迟模式**：默认线性相位 FIR 延迟约 500 µs，
> 低延迟 IIR 约 80 µs。改一根线省 420 µs。

### ADC —— 三个旋钮

12 bit，三通道扫描 + DMA，约 1 kHz 更新率即可。平滑与死区由 `sv_knobs.c` 处理。

### 时钟

音频时钟需能同时准确产生 3.072 MHz（DFSDM）与 48 kHz 帧率（SAI）。
建议用 PLL2/PLL3 的专用音频时钟分支，勿从主系统时钟分频凑数。

## 集成骨架

```c
static sv_audio_t  g_audio;
static sv_knobs_t  g_knobs;
static sv_params_t g_params;

void app_init(void)
{
    sv_params_defaults(&g_params, 48000.0f);
    sv_knobs_init(&g_knobs);
    sv_audio_init(&g_audio, &g_params);
}

/* DFSDM DMA 半满/全满回调 */
void on_audio_block(const int32_t *bone, const int32_t *air, int32_t *out)
{
    sv_audio_process_block(&g_audio, bone, air, out, SV_BLOCK_SAMPLES);
}

/* 主循环，约 1 kHz */
void on_knob_tick(uint16_t mix, uint16_t tone, uint16_t out)
{
    if (sv_knobs_update(&g_knobs, mix, tone, out, &g_params)) {
        /* 注意是 update_coeffs 而非 init —— 后者会清零滤波器状态，
         * 演出中转旋钮就会听到咔哒声。 */
        sv_chain_update_coeffs(&g_audio.chain, &g_params);
    }
}
```

## 尚未实现

以下在方案中已列为必做，但需要硬件到位后才能开发和验证：

- **自适应啸叫抑制**（LMS 自适应陷波）—— 保留的算力余量正是为它准备的
- **旁路保护** —— DSP 异常时把气导麦信号模拟旁路到输出，避免演出中彻底哑掉
- **幻象电源耐受** —— 硬件设计事项，非固件
- 时钟配置、DMA 双缓冲切换、ADC 扫描的实际调试

## 一个待决的设计问题

限幅器是**反馈式、无前瞻**的，起音瞬间增益尚未压下来，会有过冲。对拍用例 8
（输出旋钮开到 +12 dB 的过载信号）实测峰值达 **+18.4 dBFS**。

对"保护性限幅器"而言这是个真实缺口。三条可选路径：

1. **加前瞻** —— 把信号延迟一个起音时间，增益提前压下。代价是增加约 1 ms 延迟，
   相对当前 1.5 ms 的总预算不算小。
2. **末级硬饱和** —— 平滑限幅器管住常态电平，硬饱和兜住瞬态。`sv_audio.c` 的输出
   转换已经做了饱和（并计数 `output_clips`），所以**不会回绕成爆音**，但瞬态会削顶。
3. **缩短起音时间** —— 最省事，但会影响常态音质。

当前实现是路径 2 的状态：安全（不会回绕），但瞬态会削顶失真。选哪条取决于实测中
过冲有多频繁，建议阶段 0 用真实演唱信号量一下 `output_clips` 的增长速率再定。

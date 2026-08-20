# 自声还原演出设备 — DSP 参考实现

通过骨导 + 气导双通道采集，实时重建演唱者"自己听到的那个声音"，输出至调音台。

📄 **完整硬件设计方案：[docs/design.html](docs/design.html)**（含原理推导、器件选型、采样规格、
延迟预算、分阶段开发计划；克隆后用浏览器打开）。本仓库其余部分是**软件实现**。

## 当前状态

硬件（V2S200D 评估套件、STM32 板）尚未到货。但这个项目真正的难点从来不是代码，
而是**参数**——骨导补偿 EQ 的曲线长什么样、混合比多少才"像"。

所以先做的是一套与将来固件结构**逐样本一致**的 Python 参考实现：

| 能做 | 状态 |
| --- | --- |
| 处理链跑通、参数可调 | ✅ 已完成 |
| 分块处理与整段结果一致（固件移植前提） | ✅ 已验证 |
| 导出 C 系数给固件 | ✅ 已完成 |
| 合成信号验证代码正确性 | ✅ 已完成 |
| **用真实录音判断音色是否逼真** | ⛔ 等评估套件 |
| STM32 固件 | ⛔ 等硬件 + ARM 工具链 |

**关键设计约束**：Python 实现刻意不使用 `scipy.signal`，而是自己实现 Direct Form I
双二阶，系数排列与 CMSIS-DSP 的 `arm_biquad_cascade_df1_f32` 完全相同。目的是杜绝
"仿真好听、上机不对"——PC 上调出来的参数可以原样搬到固件。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

跑验证：

```bash
.venv/Scripts/python.exe tests/test_chain.py
```

生成合成测试信号并处理（硬件到货前用这条链路验证代码）：

```bash
.venv/Scripts/python.exe tools/make_testsig.py out/testsig.wav --seconds 3
.venv/Scripts/python.exe tools/process_wav.py out/testsig.wav out/out.wav --sweep-mix
```

导出固件用的 C 系数：

```bash
.venv/Scripts/python.exe tools/process_wav.py out/testsig.wav out/out.wav --export-c out/sv_coeffs.h
```

## 目录结构

```
selfvoice/
  biquad.py    双二阶级联，Direct Form I，CMSIS 系数约定
  design.py    RBJ Audio EQ Cookbook 滤波器设计
  params.py    参数定义 + 三个旋钮的映射曲线
  chain.py     完整处理链（骨导路 / 气导路 / 延迟 / 混合 / 限幅）
  export.py    导出 C 头文件
  wavio.py     WAV 读写（16/24/32 bit，仅用标准库）
  console.py   Windows 控制台 UTF-8 修正
tools/
  make_testsig.py  合成骨导/气导测试信号
  process_wav.py   离线处理 CLI（左=骨导，右=气导）
tests/
  test_chain.py    正确性验证，直接 python 运行，不需要 pytest
```

## 三个旋钮

对应方案第 09 节的面板设计。`params.py` 里的映射函数就是固件将要实现的逻辑。

| 旋钮 | 参数 | 范围 |
| --- | --- | --- |
| 混合比 | `--mix` | 0 = 纯气导，1 = 纯骨导 |
| 音色/厚度 | `--tone` | 0 = 薄亮，1 = 厚暗（宏控制，同时移动多个 EQ 频点） |
| 输出电平 | `--output-knob` | 0 = −40 dB，0.75 ≈ 0 dB，1 = +12 dB |

## 一个需要实测定夺的设计选择

气导路要延迟多少，取决于目标是什么，代码两种都支持（`--delay-mode`）：

- **`natural`（默认，≈423 µs）** — 复现本人的自然听觉时序。骨导几乎瞬时到达耳蜗，
  而气导声要绕过面部传到自己耳道（约 17 cm）；我们的麦克风在嘴外 2.5 cm 就拾到了，
  **比耳朵早**，所以要补上这段差。
- **`coherent`（≈73 µs）** — 只补偿两个传感器之间的实际路径差，两路严格对齐求和，
  声音更"干净"但不如 natural 忠实。

哪个更好听是听感问题，阶段 0 拿真实录音用耳朵判定。

> 早期讨论中曾按"麦克风距嘴 20–30 cm"估出约 0.8 ms，那是台式麦的几何。方案最终确定
> 的是头戴麦（嘴角 2–3 cm），该估算高了一个数量级，已在代码中按正确几何重新推导。

## 默认参数的可信度

`params.py` 里每个默认值都注明了来源。简要说：

- **有依据**：骨导 +20 dB 归一化（V2S200D 用户指南）、混合比 0.5（Békésy 等研究指出
  骨导与气导贡献大致相当）、气导延迟（几何推导）、近讲补偿默认关闭（全指向 MEMS
  本就没有近讲效应）。
- **纯占位，等实测**：骨导补偿 EQ 的两个频点、嘴→耳补偿的搁架参数、音色宏控制的
  曲线形状。这些是阶段 0 和阶段 1 要解决的核心问题。

## 下一步

1. 采购 V2S200D 评估套件（含 V2S 传感器 + MEMS 麦 + PDM→USB 盒，插电脑即认成
   立体声录音设备，左=骨导 右=气导）。
2. 录制真实双通道信号，用 `process_wav.py --sweep-mix` 导出多个混合比版本做 A/B。
3. 参数收敛后 `--export-c` 导出系数，进入固件阶段。

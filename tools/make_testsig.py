"""生成合成的骨导 / 气导双通道测试信号。

用途：在评估套件到货前验证处理链跑得通、系数设计没错、分块处理连续。

**这不能替代真实录音。** 合成信号里的"骨导"只是把同一激励源低通并抬升
低频，与真实颅骨传导没有物理对应关系。它能回答"代码对不对"，回答不了
"声音像不像" —— 后者是阶段 0 拿真实录音才能做的事。

用法::

    python tools/make_testsig.py out/testsig.wav --seconds 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice import design  # noqa: E402
from selfvoice.biquad import BiquadCascade  # noqa: E402
from selfvoice.console import setup as _console_setup  # noqa: E402
from selfvoice.wavio import write_wav  # noqa: E402

# 一段简单的旋律，用来听清中低频"厚度"的变化（音名 / Hz）
MELODY_HZ = [196.0, 220.0, 246.9, 261.6, 246.9, 220.0, 196.0, 174.6]

# 男声典型的前三个共振峰
FORMANTS = [(700.0, 6.0, 12.0), (1220.0, 8.0, 9.0), (2600.0, 9.0, 6.0)]


def glottal_source(fs: float, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """谐波丰富的激励源 —— 带轻微颤音与气声的锯齿波近似。"""
    n = int(fs * seconds)
    t = np.arange(n) / fs

    note_len = seconds / len(MELODY_HZ)
    f0 = np.zeros(n)
    for i, hz in enumerate(MELODY_HZ):
        lo = int(i * note_len * fs)
        hi = int(min((i + 1) * note_len * fs, n))
        f0[lo:hi] = hz
    # 颤音：6 Hz、约 ±2%
    f0 = f0 * (1.0 + 0.02 * np.sin(2.0 * np.pi * 6.0 * t))

    phase = np.cumsum(2.0 * np.pi * f0 / fs)
    # 带限锯齿：叠加谐波到 8 kHz 以下，避免混叠
    src = np.zeros(n)
    for k in range(1, 41):
        if np.max(f0) * k > 8000.0:
            break
        src += np.sin(k * phase) / k
    src /= np.max(np.abs(src)) + 1e-12

    # 气声成分
    breath = rng.normal(0.0, 1.0, n) * 0.02

    # 音符包络，避免咔哒声
    env = np.ones(n)
    fade = int(0.01 * fs)
    for i in range(len(MELODY_HZ)):
        lo = int(i * note_len * fs)
        hi = int(min((i + 1) * note_len * fs, n))
        seg = hi - lo
        if seg > 2 * fade:
            env[lo:lo + fade] = np.linspace(0, 1, fade)
            env[hi - fade:hi] = np.linspace(1, 0, fade)

    return (src + breath) * env


def make_pair(fs: float, seconds: float, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """返回 (bone, air) 两路合成信号。"""
    rng = np.random.default_rng(seed)
    src = glottal_source(fs, seconds, rng)

    # 气导：激励源经共振峰滤波 —— 近似嘴外拾到的声音
    air_stages = [design.peaking(f, q, g, fs) for f, q, g in FORMANTS]
    air = BiquadCascade(np.concatenate(air_stages)).process(src)

    # 骨导：同一激励源，低通 + 低频抬升，并叠加少量佩戴摩擦噪声。
    # 电平刻意做低约 20 dB，复现 V2S200D 相对标准 PDM 麦的电平差，
    # 好让处理链里的 +20 dB 归一化增益有东西可补。
    bone_stages = design.butterworth_lowpass(2200.0, fs, 2)
    bone_stages.append(design.low_shelf(400.0, 0.707, 8.0, fs))
    bone = BiquadCascade(np.concatenate(bone_stages)).process(src)
    rumble = rng.normal(0.0, 1.0, bone.shape[0]).astype(np.float32)
    rumble = BiquadCascade(design.lowpass(45.0, 0.707, fs)).process(rumble) * 0.35
    bone = (bone + rumble) * (10.0 ** (-20.0 / 20.0))

    peak = max(np.max(np.abs(air)), 1e-9)
    air = air / peak * 0.5
    bone = bone / peak * 0.5

    return bone.astype(np.float32), air.astype(np.float32)


def main() -> int:
    _console_setup()
    ap = argparse.ArgumentParser(description="生成合成的骨导/气导测试信号")
    ap.add_argument("output", type=Path, help="输出 WAV 路径（立体声：左=骨导，右=气导）")
    ap.add_argument("--fs", type=float, default=48000.0, help="采样率，默认 48000")
    ap.add_argument("--seconds", type=float, default=3.0, help="时长，默认 3 秒")
    ap.add_argument("--bits", type=int, default=24, choices=[16, 24, 32])
    args = ap.parse_args()

    print(f"生成 {args.seconds:.1f} s @ {args.fs:.0f} Hz 合成信号 …")
    bone, air = make_pair(args.fs, args.seconds)
    stereo = np.stack([bone, air], axis=1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.output, stereo, int(args.fs), bits=args.bits)

    print(f"  左声道 骨导  峰值 {20 * np.log10(np.max(np.abs(bone)) + 1e-12):+.1f} dBFS")
    print(f"  右声道 气导  峰值 {20 * np.log10(np.max(np.abs(air)) + 1e-12):+.1f} dBFS")
    print(f"已写出 {args.output}")
    print()
    print("注意：这是合成信号，只能验证代码正确性，不能用来判断音色是否逼真。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

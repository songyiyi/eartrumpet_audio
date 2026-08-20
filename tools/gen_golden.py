"""生成黄金向量，供固件 C 实现与 Python 参考实现逐位对拍。

这是整个移植过程中最重要的一道验证：如果 C 与 Python 在相同输入下输出的
每一个 float 都逐位相同，就说明移植没有引入任何数值偏差；反之则说明某处
运算次序、精度或状态处理不一致，而这类差异在真机上极难定位。

刻意传旋钮位置而非直接传参数：这样连参数派生逻辑（宏控制曲线、dB→线性
换算、RBJ 系数设计）也一并纳入对拍范围。

用法::

    python tools/gen_golden.py firmware/test/golden.bin

二进制格式（小端）::

    char[4] magic  = "SVGV"
    u32     version = 1
    u32     n_cases
    每个 case:
        f32 fs
        f32 mix, tone, output      旋钮位置 0..1
        u32 delay_mode             0 = natural, 1 = coherent
        u32 limiter_enabled
        u32 n_samples
        u32 block                  分块处理的块长，0 = 整段
        f32 bone[n], air[n], expected[n]
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice import ChainParams, SelfVoiceChain  # noqa: E402
from selfvoice.console import setup as _console_setup  # noqa: E402
from selfvoice.params import (  # noqa: E402
    apply_mix_knob,
    apply_output_knob,
    apply_tone_knob,
)

FS = 48000.0

# (名称, mix, tone, output, delay_mode, limiter_on, n, block, 信号类型)
CASES = [
    ("默认参数 / 整段",      0.5,  0.5,  0.75, "natural",  True,  2048, 0,   "noise"),
    ("默认参数 / 块长32",    0.5,  0.5,  0.75, "natural",  True,  2048, 32,  "noise"),
    ("偏骨导 / 厚暗",        0.8,  0.9,  0.75, "natural",  True,  2048, 64,  "noise"),
    ("偏气导 / 薄亮",        0.2,  0.1,  0.75, "natural",  True,  2048, 16,  "noise"),
    ("纯气导",               0.0,  0.5,  0.75, "natural",  True,  1024, 0,   "noise"),
    ("纯骨导",               1.0,  0.5,  0.75, "natural",  True,  1024, 0,   "noise"),
    ("coherent 延迟模式",    0.5,  0.5,  0.75, "coherent", True,  1024, 0,   "noise"),
    ("限幅器关闭",           0.5,  0.5,  0.75, "natural",  False, 1024, 0,   "noise"),
    ("过载 / 触发限幅",      0.5,  0.5,  1.0,  "natural",  True,  4096, 0,   "loud"),
    ("正弦扫频",             0.5,  0.5,  0.75, "natural",  True,  4096, 128, "sweep"),
    ("旋钮端点 / 全零输入",  0.0,  0.0,  0.0,  "natural",  True,  512,  0,   "silence"),
]


def make_signal(kind: str, n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    if kind == "silence":
        z = np.zeros(n, dtype=np.float32)
        return z, z.copy()
    if kind == "loud":
        t = np.arange(n) / FS
        bone = (0.9 * np.sin(2 * np.pi * 180.0 * t)).astype(np.float32)
        air = (1.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        return bone, air
    if kind == "sweep":
        t = np.arange(n) / FS
        f = 80.0 + (6000.0 - 80.0) * (t / max(t[-1], 1e-9))
        ph = np.cumsum(2 * np.pi * f / FS)
        s = np.sin(ph).astype(np.float32)
        return (s * np.float32(0.05)), (s * np.float32(0.3))
    # noise
    bone = rng.normal(0.0, 0.05, n).astype(np.float32)
    air = rng.normal(0.0, 0.25, n).astype(np.float32)
    return bone, air


def build_params(mix: float, tone: float, out_knob: float,
                 delay_mode: str, limiter_on: bool) -> ChainParams:
    p = ChainParams(fs=FS)
    apply_mix_knob(p, mix)
    apply_tone_knob(p, tone)
    apply_output_knob(p, out_knob)
    p.air.delay_mode = delay_mode
    p.limiter.enabled = limiter_on
    return p


def main() -> int:
    _console_setup()
    ap = argparse.ArgumentParser(description="生成 C/Python 对拍黄金向量")
    ap.add_argument("output", type=Path, nargs="?",
                    default=Path("firmware/test/golden.bin"))
    args = ap.parse_args()

    blobs: list[bytes] = []

    for i, (name, mix, tone, ok, dmode, lim, n, block, kind) in enumerate(CASES):
        bone, air = make_signal(kind, n, seed=100 + i)
        params = build_params(mix, tone, ok, dmode, lim)

        chain = SelfVoiceChain(params)
        if block:
            parts = [chain.process(bone[j:j + block], air[j:j + block])
                     for j in range(0, n, block)]
            expected = np.concatenate(parts)
        else:
            expected = chain.process(bone, air)

        blobs.append(
            struct.pack("<ffffIIII", FS, mix, tone, ok,
                        0 if dmode == "natural" else 1,
                        1 if lim else 0, n, block)
            + bone.astype("<f4").tobytes()
            + air.astype("<f4").tobytes()
            + expected.astype("<f4").tobytes()
        )

        peak = float(np.max(np.abs(expected))) if n else 0.0
        peak_db = 20 * np.log10(peak) if peak > 0 else float("-inf")
        print(f"  [{i:2d}] {name:<22} n={n:<5} block={block:<4} "
              f"峰值 {peak_db:+7.2f} dBFS")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as f:
        f.write(b"SVGV")
        f.write(struct.pack("<II", 1, len(CASES)))
        for b in blobs:
            f.write(b)

    size = args.output.stat().st_size
    print()
    print(f"已写出 {args.output}  ({size:,} 字节，{len(CASES)} 个用例)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

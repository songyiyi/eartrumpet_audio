"""离线处理：立体声输入（左=骨导，右=气导）→ 单声道自声还原输出。

评估套件到手后，这就是阶段 0 的主力工具：录一段双通道，反复跑不同参数，
戴封闭耳机 A/B 对比，直到"对，这就是我听到的自己"。

用法::

    python tools/process_wav.py in.wav out.wav --mix 0.6 --tone 0.7
    python tools/process_wav.py in.wav out.wav --sweep-mix        # 批量导出多个混合比
    python tools/process_wav.py in.wav out.wav --export-c coeffs.h
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice import ChainParams, SelfVoiceChain, read_wav, write_wav  # noqa: E402
from selfvoice.console import setup as _console_setup  # noqa: E402
from selfvoice.export import export_c_header  # noqa: E402
from selfvoice.params import (  # noqa: E402
    apply_mix_knob,
    apply_output_knob,
    apply_tone_knob,
)


def build_params(args, fs: float) -> ChainParams:
    p = ChainParams(fs=fs)
    apply_mix_knob(p, args.mix)
    apply_tone_knob(p, args.tone)
    apply_output_knob(p, args.output_knob)
    p.air.delay_mode = args.delay_mode
    p.limiter.enabled = not args.no_limiter
    if args.proximity_comp:
        p.air.proximity_comp_enabled = True
    return p


def run_once(bone, air, params: ChainParams, out_path: Path, bits: int,
             block: int | None) -> None:
    chain = SelfVoiceChain(params)
    if block:
        # 分块处理 —— 复现固件按 DMA 块运行的方式
        chunks = [
            chain.process(bone[i:i + block], air[i:i + block])
            for i in range(0, bone.shape[0], block)
        ]
        out = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    else:
        out = chain.process(bone, air)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out_path, out, int(params.fs), bits=bits)

    peak_db = 20.0 * np.log10(np.max(np.abs(out)) + 1e-12)
    print(f"  → {out_path.name}   峰值 {peak_db:+.1f} dBFS", end="")
    if params.limiter.enabled and chain.limiter.reduction_db < -0.1:
        print(f"   限幅最大衰减 {chain.limiter.reduction_db:.1f} dB", end="")
    print()


def main() -> int:
    _console_setup()
    ap = argparse.ArgumentParser(
        description="自声还原离线处理（左声道=骨导，右声道=气导）"
    )
    ap.add_argument("input", type=Path, help="输入立体声 WAV")
    ap.add_argument("output", type=Path, help="输出单声道 WAV")
    ap.add_argument("--mix", type=float, default=0.5,
                    help="混合比旋钮 0..1（0=纯气导，1=纯骨导），默认 0.5")
    ap.add_argument("--tone", type=float, default=0.5,
                    help="音色旋钮 0..1（0=薄亮，1=厚暗），默认 0.5")
    ap.add_argument("--output-knob", type=float, default=0.75,
                    help="输出电平旋钮 0..1，默认 0.75（约 0 dB）")
    ap.add_argument("--delay-mode", choices=["natural", "coherent"],
                    default="natural", help="气导延迟模式，默认 natural")
    ap.add_argument("--proximity-comp", action="store_true",
                    help="启用近讲效应补偿（仅定向咪头需要）")
    ap.add_argument("--no-limiter", action="store_true", help="关闭限幅器")
    ap.add_argument("--bits", type=int, default=24, choices=[16, 24, 32])
    ap.add_argument("--block", type=int, default=0,
                    help="分块处理的块长，0 表示整段处理。用于验证块边界连续性")
    ap.add_argument("--sweep-mix", action="store_true",
                    help="批量导出 mix=0.0/0.25/0.5/0.75/1.0 五个版本供 A/B")
    ap.add_argument("--export-c", type=Path, default=None,
                    help="同时导出 C 系数头文件到指定路径")
    args = ap.parse_args()

    samples, fs = read_wav(args.input)
    if samples.shape[1] < 2:
        print(f"错误：输入需要立体声（左=骨导，右=气导），实际为 "
              f"{samples.shape[1]} 声道", file=sys.stderr)
        return 1

    bone = samples[:, 0]
    air = samples[:, 1]
    dur = bone.shape[0] / fs
    print(f"输入 {args.input.name}  {fs:.0f} Hz  {dur:.2f} s")

    params = build_params(args, fs)
    print()
    print(SelfVoiceChain(params).describe())
    print()

    if args.sweep_mix:
        for m in (0.0, 0.25, 0.5, 0.75, 1.0):
            sweep_params = build_params(args, fs)
            apply_mix_knob(sweep_params, m)
            stem = args.output.with_suffix("")
            out_path = Path(f"{stem}_mix{int(m * 100):03d}.wav")
            run_once(bone, air, sweep_params, out_path, args.bits,
                     args.block or None)
    else:
        run_once(bone, air, params, args.output, args.bits, args.block or None)

    if args.export_c:
        path = export_c_header(SelfVoiceChain(params), args.export_c)
        print(f"  → 已导出 C 系数 {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

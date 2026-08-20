"""把 PC 上调好的系数导出为固件可直接编译的 C 头文件。

这是整套 PC 工具存在的最终目的：在 Python 里把参数调准，然后把结果原样
搬到 STM32 上，中间不经人手转录。导出的系数排列与
arm_biquad_cascade_df1_f32 的要求完全一致，可直接作为 pCoeffs 传入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .chain import SelfVoiceChain


def _fmt_stage(c: np.ndarray) -> str:
    return ", ".join(f"{v:+.10f}f" for v in c)


def export_c_header(chain: SelfVoiceChain, path: str | Path,
                    guard: str = "SELFVOICE_COEFFS_H") -> Path:
    """生成 C 头文件。返回写出的路径。"""
    p = chain.params
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    bone = chain.bone_filter.coeffs
    air = chain.air_filter.coeffs
    delay_samples = int(round(p.air.delay_seconds() * p.fs))

    lines: list[str] = []
    add = lines.append

    add("/* 由 selfvoice/export.py 自动生成，请勿手工编辑。")
    add(f" * 生成时间: {stamp}")
    add(" *")
    add(" * 系数排列符合 CMSIS-DSP arm_biquad_cascade_df1_f32 约定，每级 5 个:")
    add(" *   {b0, b1, b2, a1, a2}")
    add(" *   y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] + a1*y[n-1] + a2*y[n-2]")
    add(" *   注意 a1/a2 前为加号，已在生成时取过负号。")
    add(" */")
    add("")
    add(f"#ifndef {guard}")
    add(f"#define {guard}")
    add("")
    # 注意 .1f：整数字面量加 f 后缀在 C 里是非法的，必须写成 48000.0f
    add(f"#define SV_SAMPLE_RATE_HZ   {p.fs:.1f}f")
    add(f"#define SV_BONE_STAGES      {chain.bone_filter.num_stages}")
    add(f"#define SV_AIR_STAGES       {chain.air_filter.num_stages}")
    add(f"#define SV_AIR_DELAY_SAMPLES {delay_samples}   "
        f"/* {p.air.delay_seconds() * 1e6:.0f} us, 模式 {p.air.delay_mode} */")
    add("")
    add(f"#define SV_BONE_GAIN        {10.0 ** (p.bone.gain_db / 20.0):+.8f}f   "
        f"/* {p.bone.gain_db:+.1f} dB */")
    add(f"#define SV_AIR_GAIN         {10.0 ** (p.air.gain_db / 20.0):+.8f}f   "
        f"/* {p.air.gain_db:+.1f} dB */")
    add(f"#define SV_BONE_RATIO       {p.bone_ratio:+.8f}f")
    add(f"#define SV_LIMIT_THRESHOLD  "
        f"{10.0 ** (p.limiter.threshold_db / 20.0):+.8f}f   "
        f"/* {p.limiter.threshold_db:+.1f} dBFS */")
    add("")

    add("/* 骨导通路: 高通 -> 补偿 EQ -> 低通 */")
    add("static const float sv_bone_coeffs[SV_BONE_STAGES * 5] = {")
    for i, stage in enumerate(bone):
        add(f"    {_fmt_stage(stage)},   /* stage {i} */")
    add("};")
    add("")

    add("/* 气导通路: 嘴->耳补偿 (+ 可选近讲补偿) */")
    add("static const float sv_air_coeffs[SV_AIR_STAGES * 5] = {")
    for i, stage in enumerate(air):
        add(f"    {_fmt_stage(stage)},   /* stage {i} */")
    add("};")
    add("")
    add(f"#endif /* {guard} */")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

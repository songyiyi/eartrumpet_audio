"""处理链正确性验证。

刻意不依赖 pytest —— 直接 `python tests/test_chain.py` 即可运行，
减少环境依赖。

最重要的一条是 test_block_continuity：固件按 DMA 块运行，若分块结果与
整段结果不一致，说明某个环节的状态没有正确保持，上机必然出问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice import ChainParams, SelfVoiceChain, design  # noqa: E402
from selfvoice.biquad import BiquadCascade, cascade_response_db  # noqa: E402
from selfvoice.chain import DelayLine, PeakLimiter  # noqa: E402
from selfvoice.console import setup as _console_setup  # noqa: E402

FS = 48000.0


def test_highpass_corner():
    """二阶巴特沃斯高通在截止频率处应为 -3 dB。"""
    stages = design.butterworth_highpass(80.0, FS, 2)
    c = BiquadCascade(np.concatenate(stages))
    db = cascade_response_db(c, [80.0], FS)[0]
    assert abs(db + 3.0) < 0.3, f"80 Hz 处应约 -3 dB，实际 {db:.2f} dB"

    # 远高于截止频率处应接近 0 dB
    db_pass = cascade_response_db(c, [2000.0], FS)[0]
    assert abs(db_pass) < 0.2, f"通带应接近 0 dB，实际 {db_pass:.2f} dB"

    # 远低于截止频率处应大幅衰减（二阶 = 40 dB/十倍频）
    db_stop = cascade_response_db(c, [8.0], FS)[0]
    assert db_stop < -35.0, f"8 Hz 处衰减不足，实际 {db_stop:.2f} dB"


def test_lowpass_corner():
    stages = design.butterworth_lowpass(3000.0, FS, 2)
    c = BiquadCascade(np.concatenate(stages))
    db = cascade_response_db(c, [3000.0], FS)[0]
    assert abs(db + 3.0) < 0.3, f"3 kHz 处应约 -3 dB，实际 {db:.2f} dB"


def test_peaking_gain():
    """峰值均衡在中心频率处应达到设定增益。"""
    for gain_db in (-6.0, -2.0, 3.0, 9.0):
        c = BiquadCascade(design.peaking(1000.0, 1.0, gain_db, FS))
        db = cascade_response_db(c, [1000.0], FS)[0]
        assert abs(db - gain_db) < 0.05, \
            f"设定 {gain_db:+.1f} dB，实测 {db:+.2f} dB"


def test_shelf_gain():
    """搁架滤波在远端应达到设定增益的全量。"""
    c = BiquadCascade(design.low_shelf(500.0, 0.707, 6.0, FS))
    db = cascade_response_db(c, [20.0], FS)[0]
    assert abs(db - 6.0) < 0.2, f"低搁架远端应 +6 dB，实际 {db:+.2f} dB"

    c = BiquadCascade(design.high_shelf(4000.0, 0.707, -6.0, FS))
    db = cascade_response_db(c, [20000.0], FS)[0]
    assert abs(db + 6.0) < 0.2, f"高搁架远端应 -6 dB，实际 {db:+.2f} dB"


def test_bypass_is_transparent():
    c = BiquadCascade(design.bypass())
    x = np.random.default_rng(1).normal(0, 0.2, 512).astype(np.float32)
    y = c.process(x)
    assert np.allclose(x, y, atol=1e-7), "直通级不应改变信号"


def test_delay_line():
    """延迟线应把冲激精确搬移指定样本数，且跨块调用保持连续。"""
    d = DelayLine(20)
    x = np.zeros(64, dtype=np.float32)
    x[0] = 1.0
    y = d.process(x)
    idx = int(np.argmax(np.abs(y)))
    assert idx == 20, f"冲激应出现在第 20 个样本，实际第 {idx} 个"

    # 跨块：冲激落在块边界附近也应正确
    d2 = DelayLine(20)
    a = np.zeros(10, dtype=np.float32)
    a[5] = 1.0
    b = np.zeros(40, dtype=np.float32)
    out = np.concatenate([d2.process(a), d2.process(b)])
    idx2 = int(np.argmax(np.abs(out)))
    assert idx2 == 25, f"跨块延迟应落在第 25 个样本，实际第 {idx2} 个"


def test_limiter_holds_threshold():
    """限幅器应把明显过载的信号压回阈值附近。"""
    lim = PeakLimiter(threshold_db=-1.0, attack_ms=1.0, release_ms=50.0, fs=FS)
    n = int(FS * 0.5)
    t = np.arange(n) / FS
    x = (2.0 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)  # +6 dBFS
    y = lim.process(x)

    # 跳过起始的 attack 段再评估稳态
    steady = y[int(FS * 0.05):]
    peak_db = 20.0 * np.log10(np.max(np.abs(steady)) + 1e-12)
    assert peak_db < 0.0, f"限幅后仍达 {peak_db:+.2f} dBFS"
    assert peak_db > -3.0, f"限幅过度，只剩 {peak_db:+.2f} dBFS"


def test_block_continuity():
    """★ 关键：分块处理必须与整段处理逐样本一致。

    固件按 DMA 块运行。若这条不成立，说明某处状态未跨调用保持，
    上机会在每个块边界产生不连续（听起来是周期性的咔哒声）。
    """
    rng = np.random.default_rng(42)
    n = 4096
    bone = rng.normal(0.0, 0.05, n).astype(np.float32)
    air = rng.normal(0.0, 0.2, n).astype(np.float32)

    params = ChainParams(fs=FS)

    whole = SelfVoiceChain(params).process(bone, air)

    for block in (16, 32, 64, 128, 333):
        chain = SelfVoiceChain(params)
        chunks = [
            chain.process(bone[i:i + block], air[i:i + block])
            for i in range(0, n, block)
        ]
        blocked = np.concatenate(chunks)
        assert blocked.shape == whole.shape
        max_err = float(np.max(np.abs(blocked - whole)))
        assert max_err < 1e-6, \
            f"块长 {block} 与整段结果不一致，最大误差 {max_err:.3e}"


def test_mix_ratio_extremes():
    """混合比取极值时应分别等于纯骨导 / 纯气导路径。"""
    rng = np.random.default_rng(3)
    n = 1024
    bone = rng.normal(0.0, 0.05, n).astype(np.float32)
    air = rng.normal(0.0, 0.2, n).astype(np.float32)

    p_bone = ChainParams(fs=FS)
    p_bone.bone_ratio = 1.0
    p_bone.limiter.enabled = False
    only_bone = SelfVoiceChain(p_bone).process(bone, air)

    p_air = ChainParams(fs=FS)
    p_air.bone_ratio = 0.0
    p_air.limiter.enabled = False
    only_air = SelfVoiceChain(p_air).process(bone, air)

    # 纯气导时，把气导输入置零应得到全零输出
    zero = SelfVoiceChain(p_air).process(bone, np.zeros_like(air))
    assert np.max(np.abs(zero)) < 1e-6, "bone_ratio=0 时骨导不应泄漏到输出"

    zero_b = SelfVoiceChain(p_bone).process(np.zeros_like(bone), air)
    assert np.max(np.abs(zero_b)) < 1e-6, "bone_ratio=1 时气导不应泄漏到输出"

    assert np.max(np.abs(only_bone)) > 1e-6
    assert np.max(np.abs(only_air)) > 1e-6


def test_delay_mode_values():
    """两种延迟模式应给出预期量级的延迟。"""
    p = ChainParams(fs=FS)

    p.air.delay_mode = "natural"
    us = p.air.delay_seconds() * 1e6
    assert 380.0 < us < 460.0, f"natural 模式应约 420 µs，实际 {us:.0f} µs"

    p.air.delay_mode = "coherent"
    us = p.air.delay_seconds() * 1e6
    assert 60.0 < us < 90.0, f"coherent 模式应约 73 µs，实际 {us:.0f} µs"


def test_knob_ranges():
    """旋钮映射在端点处不应产生非法参数。"""
    from selfvoice.params import apply_mix_knob, apply_output_knob, apply_tone_knob

    for pos in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = ChainParams(fs=FS)
        apply_mix_knob(p, pos)
        apply_tone_knob(p, pos)
        apply_output_knob(p, pos)
        assert 0.0 <= p.bone_ratio <= 1.0
        assert 0.0 < p.bone.lp_hz < FS / 2.0
        # 构建不应抛异常
        SelfVoiceChain(p)

    # 越界输入应被夹紧而非报错
    p = ChainParams(fs=FS)
    apply_mix_knob(p, -5.0)
    assert p.bone_ratio == 0.0
    apply_mix_knob(p, 5.0)
    assert p.bone_ratio == 1.0


def main() -> int:
    _console_setup()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for fn in tests:
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"  FAIL  {name}\n          {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
        else:
            print(f"  ok    {name}")

    print()
    if failed:
        print(f"{len(failed)} / {len(tests)} 项失败")
        return 1
    print(f"全部 {len(tests)} 项通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

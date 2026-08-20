"""完整的自声还原处理链。

对应方案第 06 节的框图：

    骨导 ─→ 增益 ─→ 高通 ─→ 补偿EQ ─→ 低通 ──────────────┐
                                                          ├─→ 混合 ─→ 限幅 ─→ 输出增益
    气导 ─→ 增益 ─→ 嘴→耳补偿 ─→ (近讲补偿) ─→ 延迟 ─────┘

设计上刻意支持**分块处理**：所有环节的状态都跨调用保持，因此
process(x) 与逐块调用 process(x[0:64]), process(x[64:128]) … 的结果
必须完全一致。固件按 DMA 块运行，这个性质是移植正确性的前提，
tests/ 里有对应的验证。
"""

from __future__ import annotations

import math

import numpy as np

from . import design
from .biquad import BiquadCascade
from .params import ChainParams

# 气导路搁架滤波器的 Q。
#
# 必须是 float32：固件里写的是 0.707f，其数值为 0.70700001716613770，
# 而 Python 的字面量 0.707 是 double，为 0.70699999999999996。两者不同，
# 会导致气导路系数有约 1e-7 的偏差 —— 对拍测试正是靠这个量级的差异
# 把它揪出来的（差异恰好从延迟长度之后的样本开始出现）。
_SHELF_Q = np.float32(0.707)


class DelayLine:
    """整数样本延迟线。

    刻意只做整数延迟 —— 固件里也会这么做（环形缓冲区读指针偏移，零成本）。
    48 kHz 下量化误差最大 ±10 µs，相对 420 µs 的目标延迟可以忽略。
    """

    def __init__(self, delay_samples: int) -> None:
        self.n = int(max(0, delay_samples))
        self.buf = np.zeros(self.n, dtype=np.float32) if self.n else None

    def reset(self) -> None:
        if self.buf is not None:
            self.buf[:] = 0.0

    def process(self, x) -> np.ndarray:
        xin = np.asarray(x, dtype=np.float32)
        if self.n == 0:
            return xin.copy()
        combined = np.concatenate([self.buf, xin])
        out = combined[: xin.shape[0]]
        self.buf = combined[xin.shape[0]:].astype(np.float32)
        return out.astype(np.float32)


class PeakLimiter:
    """保护性峰值限幅器。

    刻意做得简单直接 —— 它的职责只是防削波。音乐性的压缩留给调音台，
    见方案第 11 节的处理边界划分。
    """

    def __init__(self, threshold_db: float, attack_ms: float,
                 release_ms: float, fs: float) -> None:
        # 系数按 double 推导一次，随后一律降为 float32。
        # 刻意统一精度：固件里全程是 float，若这里混用 double 与 float32，
        # C 实现就永远无法与 Python 逐位一致，对拍验证也就失去意义。
        self.threshold = np.float32(10.0 ** (float(threshold_db) / 20.0))
        self.att = np.float32(
            math.exp(-1.0 / (max(float(attack_ms), 1e-3) * 1e-3 * float(fs)))
        )
        self.rel = np.float32(
            math.exp(-1.0 / (max(float(release_ms), 1e-3) * 1e-3 * float(fs)))
        )
        self.reset()

    def reset(self) -> None:
        self.gain = np.float32(1.0)
        self.reduction_db = 0.0  # 最近一次处理的最大增益衰减，便于监看

    def process(self, x) -> np.ndarray:
        xin = np.asarray(x, dtype=np.float32)
        out = np.empty_like(xin)
        g = self.gain
        thr = self.threshold
        att, rel = self.att, self.rel
        one = np.float32(1.0)
        min_g = one

        for n in range(xin.shape[0]):
            v = xin[n]
            mag = abs(v)
            target = np.float32(thr / mag) if mag > thr else one
            # 需要压下去时用 attack，放回来时用 release
            coef = att if target < g else rel
            g = np.float32(coef * g + (one - coef) * target)
            out[n] = v * g
            if g < min_g:
                min_g = g

        self.gain = g
        self.reduction_db = 20.0 * math.log10(max(float(min_g), 1e-6))
        return out


class SelfVoiceChain:
    """自声还原处理链。"""

    def __init__(self, params: ChainParams | None = None) -> None:
        self.params = params if params is not None else ChainParams()
        self.rebuild()

    # -- 构建 -------------------------------------------------------------

    def rebuild(self) -> None:
        """按当前参数重建所有滤波器与状态。

        固件里旋钮转动时也走这条路径 —— RBJ 公式是封闭形式，重算几十个
        系数只要几微秒，不需要预存查找表。
        """
        p = self.params
        fs = p.fs

        bone_stages: list[np.ndarray] = []
        bone_stages += design.butterworth_highpass(p.bone.hp_hz, fs, p.bone.hp_order)
        for f0, q, gain_db in p.bone.eq:
            bone_stages.append(design.peaking(f0, q, gain_db, fs))
        bone_stages += design.butterworth_lowpass(p.bone.lp_hz, fs, p.bone.lp_order)
        self.bone_filter = BiquadCascade(np.concatenate(bone_stages))

        air_stages: list[np.ndarray] = [
            design.low_shelf(p.air.mouth_to_ear_low_shelf_hz, _SHELF_Q,
                             p.air.mouth_to_ear_low_shelf_db, fs),
            design.high_shelf(p.air.mouth_to_ear_high_shelf_hz, _SHELF_Q,
                              p.air.mouth_to_ear_high_shelf_db, fs),
        ]
        if p.air.proximity_comp_enabled:
            air_stages.append(
                design.low_shelf(p.air.proximity_comp_hz, _SHELF_Q,
                                 p.air.proximity_comp_db, fs)
            )
        self.air_filter = BiquadCascade(np.concatenate(air_stages))

        delay_samples = int(round(p.air.delay_seconds() * fs))
        self.air_delay = DelayLine(delay_samples)

        self.limiter = PeakLimiter(p.limiter.threshold_db, p.limiter.attack_ms,
                                   p.limiter.release_ms, fs)

        # float() 强转的理由同 design.py：dB 值可能是 np.float32，若不转成
        # double，10**x 会退化到 float32 精度计算，而固件 sv_chain.c 的
        # db_to_lin() 是 (float)pow(10.0, (double)db/20.0) —— 双精度算完再降
        # 一次。舍入点不同，对拍就会失败。
        self.bone_gain = np.float32(10.0 ** (float(p.bone.gain_db) / 20.0))
        self.air_gain = np.float32(10.0 ** (float(p.air.gain_db) / 20.0))
        self.output_gain = np.float32(10.0 ** (float(p.output_gain_db) / 20.0))

    def reset(self) -> None:
        self.bone_filter.reset()
        self.air_filter.reset()
        self.air_delay.reset()
        self.limiter.reset()

    # -- 运行 -------------------------------------------------------------

    def process(self, bone, air) -> np.ndarray:
        """处理一段（或一块）双通道信号，返回单声道输出。

        bone/air 必须等长。状态跨调用保持，可安全地分块调用。
        """
        b = np.asarray(bone, dtype=np.float32)
        a = np.asarray(air, dtype=np.float32)
        if b.shape != a.shape:
            raise ValueError(f"骨导与气导长度不一致: {b.shape} vs {a.shape}")

        b = self.bone_filter.process(b * self.bone_gain)
        a = self.air_delay.process(self.air_filter.process(a * self.air_gain))

        ratio = np.float32(min(max(self.params.bone_ratio, 0.0), 1.0))
        mixed = b * ratio + a * (np.float32(1.0) - ratio)
        mixed = mixed * self.output_gain

        if self.params.limiter.enabled:
            mixed = self.limiter.process(mixed)

        return mixed

    # -- 观测 -------------------------------------------------------------

    def describe(self) -> str:
        p = self.params
        delay_us = p.air.delay_seconds() * 1e6
        delay_samples = int(round(p.air.delay_seconds() * p.fs))
        lines = [
            f"采样率            {p.fs:.0f} Hz",
            f"骨导占比          {p.bone_ratio:.2f}"
            f"  (气导 {1 - p.bone_ratio:.2f})",
            f"骨导增益          {p.bone.gain_db:+.1f} dB"
            f"   [{self.bone_filter.num_stages} 级 biquad]",
            f"骨导高通 / 低通   {p.bone.hp_hz:.0f} Hz / {p.bone.lp_hz:.0f} Hz",
            f"骨导补偿 EQ       "
            + ", ".join(f"{f:.0f}Hz Q{q:.2f} {g:+.1f}dB" for f, q, g in p.bone.eq),
            f"气导滤波          {self.air_filter.num_stages} 级 biquad"
            f"  (近讲补偿 {'开' if p.air.proximity_comp_enabled else '关'})",
            f"气导延迟          {delay_us:.0f} µs = {delay_samples} 样本"
            f"   [模式 {p.air.delay_mode}]",
            f"限幅              {'开' if p.limiter.enabled else '关'}"
            f"  阈值 {p.limiter.threshold_db:+.1f} dBFS",
            f"输出增益          {p.output_gain_db:+.1f} dB",
        ]
        return "\n".join(lines)

"""滤波器系数设计 —— RBJ Audio EQ Cookbook 公式。

所有设计函数返回 CMSIS-DSP 约定的 5 元组 [b0, b1, b2, a1, a2]（已归一化到
a0=1，且 a1/a2 已取负），可直接喂给 biquad.BiquadCascade，也可直接导出为
固件用的 C 数组。

选用 RBJ 公式而非其它设计法，是因为它形式封闭、无需迭代，几十行 C 代码就能
在 MCU 上实时重算 —— 这样旋钮转动时可以即时更新系数，不需要预存查找表。
"""

from __future__ import annotations

import math

import numpy as np


def _finalize(b0: float, b1: float, b2: float,
              a0: float, a1: float, a2: float) -> np.ndarray:
    """归一化到 a0=1 并转成 CMSIS 约定（a1/a2 取负）。"""
    return np.array(
        [b0 / a0, b1 / a0, b2 / a0, -a1 / a0, -a2 / a0],
        dtype=np.float64,
    )


def _omega(f0: float, fs: float) -> tuple[float, float, float]:
    if not (0.0 < f0 < fs / 2.0):
        raise ValueError(f"截止频率 {f0} Hz 必须落在 (0, {fs / 2}) 内")
    w0 = 2.0 * math.pi * f0 / fs
    return w0, math.cos(w0), math.sin(w0)


def lowpass(f0: float, q: float, fs: float) -> np.ndarray:
    w0, cos_w0, sin_w0 = _omega(f0, fs)
    alpha = sin_w0 / (2.0 * q)
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return _finalize(b0, b1, b2, a0, a1, a2)


def highpass(f0: float, q: float, fs: float) -> np.ndarray:
    w0, cos_w0, sin_w0 = _omega(f0, fs)
    alpha = sin_w0 / (2.0 * q)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return _finalize(b0, b1, b2, a0, a1, a2)


def peaking(f0: float, q: float, gain_db: float, fs: float) -> np.ndarray:
    """峰值/陷波均衡。gain_db 为正即提升，为负即衰减。"""
    w0, cos_w0, sin_w0 = _omega(f0, fs)
    A = 10.0 ** (gain_db / 40.0)
    alpha = sin_w0 / (2.0 * q)
    b0 = 1.0 + alpha * A
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / A
    return _finalize(b0, b1, b2, a0, a1, a2)


def low_shelf(f0: float, q: float, gain_db: float, fs: float) -> np.ndarray:
    w0, cos_w0, sin_w0 = _omega(f0, fs)
    A = 10.0 ** (gain_db / 40.0)
    alpha = sin_w0 / (2.0 * q)
    two_sqrt_a_alpha = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
    b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
    a2 = (A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha
    return _finalize(b0, b1, b2, a0, a1, a2)


def high_shelf(f0: float, q: float, gain_db: float, fs: float) -> np.ndarray:
    w0, cos_w0, sin_w0 = _omega(f0, fs)
    A = 10.0 ** (gain_db / 40.0)
    alpha = sin_w0 / (2.0 * q)
    two_sqrt_a_alpha = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0)
    b2 = A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w0)
    a2 = (A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha
    return _finalize(b0, b1, b2, a0, a1, a2)


def bypass() -> np.ndarray:
    """直通级 —— 用于占位，保持级数固定（固件里级数最好是编译期常量）。"""
    return np.array([1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)


def butterworth_highpass(f0: float, fs: float, order: int = 2) -> list[np.ndarray]:
    """巴特沃斯高通，返回若干个二阶级。order 必须是偶数。

    单个 RBJ 二阶节 Q=0.707 即为二阶巴特沃斯；更高阶需要用不同 Q 的级联，
    各级 Q 由极点角度决定。
    """
    if order % 2 != 0 or order < 2:
        raise ValueError("order 必须是 >=2 的偶数")
    stages = []
    n_sections = order // 2
    for k in range(n_sections):
        theta = math.pi * (2.0 * k + 1.0) / (2.0 * order)
        q = 1.0 / (2.0 * math.sin(theta))
        stages.append(highpass(f0, q, fs))
    return stages


def butterworth_lowpass(f0: float, fs: float, order: int = 2) -> list[np.ndarray]:
    if order % 2 != 0 or order < 2:
        raise ValueError("order 必须是 >=2 的偶数")
    stages = []
    n_sections = order // 2
    for k in range(n_sections):
        theta = math.pi * (2.0 * k + 1.0) / (2.0 * order)
        q = 1.0 / (2.0 * math.sin(theta))
        stages.append(lowpass(f0, q, fs))
    return stages

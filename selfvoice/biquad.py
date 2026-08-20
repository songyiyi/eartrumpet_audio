"""双二阶（biquad）滤波器级联。

刻意不使用 scipy.signal，而是自己实现 Direct Form I —— 目的是保证在 PC 上
调出来的结果与将来 STM32 固件**逐样本一致**：同样的结构、同样的系数排列、
同样的 float32 精度。避免"仿真好听、上机不对"。

系数约定与 CMSIS-DSP 的 arm_biquad_cascade_df1_f32 完全相同，每级 5 个系数：

    {b0, b1, b2, a1, a2}

差分方程（注意 a1/a2 前面是**加号**，与教科书写法相反）：

    y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] + a1*y[n-1] + a2*y[n-2]

教科书形式为 y[n] = ... - a1'*y[n-1] - a2'*y[n-2]，因此 a1 = -a1'、a2 = -a2'。
这是移植时最经典的符号错误来源，design.py 里的设计函数已统一按本约定输出。
"""

from __future__ import annotations

import numpy as np

COEFFS_PER_STAGE = 5


class BiquadCascade:
    """float32 双二阶级联，Direct Form I。

    状态在多次 process() 调用之间保持，因此可以分块处理而不产生边界不连续
    —— 这正是固件中按 DMA 块处理的方式。
    """

    def __init__(self, coeffs) -> None:
        c = np.asarray(coeffs, dtype=np.float32)
        if c.size % COEFFS_PER_STAGE != 0:
            raise ValueError(
                f"系数个数必须是 {COEFFS_PER_STAGE} 的整数倍，收到 {c.size} 个"
            )
        self.coeffs = c.reshape(-1, COEFFS_PER_STAGE)
        self.reset()

    @property
    def num_stages(self) -> int:
        return int(self.coeffs.shape[0])

    def reset(self) -> None:
        """清零状态。每级 4 个状态量：x[n-1], x[n-2], y[n-1], y[n-2]。"""
        self.state = np.zeros((self.num_stages, 4), dtype=np.float32)

    def process(self, x) -> np.ndarray:
        """处理一段样本，返回同长度输出。

        逐样本循环，与固件实现一一对应。对离线调参的信号长度（几秒到几十秒）
        速度足够；不要用它来做实时处理。
        """
        xin = np.asarray(x, dtype=np.float32)
        out = np.empty_like(xin)
        coeffs = self.coeffs
        state = self.state

        for n in range(xin.shape[0]):
            v = xin[n]
            for s in range(coeffs.shape[0]):
                b0, b1, b2, a1, a2 = coeffs[s]
                x1, x2, y1, y2 = state[s]
                y = b0 * v + b1 * x1 + b2 * x2 + a1 * y1 + a2 * y2
                state[s, 0] = v
                state[s, 1] = x1
                state[s, 2] = y
                state[s, 3] = y1
                v = y
            out[n] = v

        return out

    def frequency_response(self, freqs, fs: float) -> np.ndarray:
        """计算级联的复频响，用于验证设计结果（不影响信号处理路径）。"""
        f = np.asarray(freqs, dtype=np.float64)
        z = np.exp(-2j * np.pi * f / fs)
        z2 = z * z
        h = np.ones_like(z, dtype=np.complex128)
        for b0, b1, b2, a1, a2 in self.coeffs.astype(np.float64):
            num = b0 + b1 * z + b2 * z2
            # 注意：CMSIS 约定下分母是 1 - a1*z - a2*z^2
            den = 1.0 - a1 * z - a2 * z2
            h *= num / den
        return h


def cascade_response_db(cascade: BiquadCascade, freqs, fs: float) -> np.ndarray:
    """便捷函数：返回以 dB 表示的幅频响应。"""
    h = cascade.frequency_response(freqs, fs)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))

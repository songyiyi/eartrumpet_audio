"""WAV 读写 —— 只用标准库，不引入额外依赖。

支持 16 / 24 / 32 位整型 PCM。24 位是重点：方案确定采集位深为 24 bit，
而 Python 标准库的 wave 模块只给出原始字节，需要自己拼装。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """读取 WAV，返回 (samples, fs)。

    samples 形状为 (n_frames, n_channels)，float32，归一化到 [-1, 1)。
    """
    path = Path(path)
    with wave.open(str(path), "rb") as w:
        n_ch = w.getnchannels()
        width = w.getsampwidth()
        fs = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        data = v.astype(np.float32) / 8388608.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的位宽: {width * 8} bit")

    return data.reshape(-1, n_ch), fs


def write_wav(path: str | Path, samples, fs: int, bits: int = 24) -> None:
    """写出 WAV。samples 为 (n_frames,) 或 (n_frames, n_channels) 的浮点数组。

    默认 24 bit —— 与方案确定的采集位深一致，导出的文件不会成为链路瓶颈。
    """
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    n_ch = x.shape[1]

    # 超出范围直接硬截，并在越界时提示 —— 静默削波是调参阶段最坏的事
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 1.0:
        print(f"  [警告] 输出峰值 {20 * np.log10(peak):+.2f} dBFS 超过满刻度，已截幅")
    x = np.clip(x, -1.0, 1.0 - 1e-9)

    if bits == 16:
        raw = (x * 32767.0).astype("<i2").tobytes()
        width = 2
    elif bits == 24:
        v = (x * 8388607.0).astype(np.int32).reshape(-1)
        b = np.empty((v.shape[0], 3), dtype=np.uint8)
        b[:, 0] = v & 0xFF
        b[:, 1] = (v >> 8) & 0xFF
        b[:, 2] = (v >> 16) & 0xFF
        raw = b.tobytes()
        width = 3
    elif bits == 32:
        raw = (x * 2147483647.0).astype("<i4").tobytes()
        width = 4
    else:
        raise ValueError(f"不支持的位宽: {bits} bit")

    with wave.open(str(Path(path)), "wb") as w:
        w.setnchannels(n_ch)
        w.setsampwidth(width)
        w.setframerate(int(fs))
        w.writeframes(raw)

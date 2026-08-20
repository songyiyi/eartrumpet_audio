"""ctypes 封装：调用编译好的固件 C 核心。

用途是给调参界面提供实时处理能力 —— Python 参考实现是逐样本循环，只有
约 1.39x 实时，无法安全驱动音频回调；同一份 C 代码编译后快出两个数量级。

额外的好处是：**试听到的就是固件本身的代码**，而不是另一套等价实现。
动态库与对拍测试用的是完全相同的源文件和编译参数（含 -ffp-contract=off），
因此其行为与已验证的固件逐位一致。

若动态库不存在，NativeChain 会抛出带构建提示的异常，由调用方决定是回退到
Python 实现还是提示用户先编译。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import numpy as np

_NATIVE_DIR = Path(__file__).resolve().parent / "_native"

# svx_get_info 的索引，与 firmware/host/sv_native.c 中的 switch 一一对应
INFO_BONE_RATIO = 0
INFO_OUTPUT_GAIN_DB = 1
INFO_BONE_LP_HZ = 2
INFO_EQ0_GAIN_DB = 3
INFO_EQ1_GAIN_DB = 4
INFO_DELAY_SAMPLES = 5
INFO_BONE_GAIN_DB = 6

_MAX_COEFF_FLOATS = 64


def _library_name() -> str:
    if sys.platform == "win32":
        return "sv_core.dll"
    if sys.platform == "darwin":
        return "libsv_core.dylib"
    return "libsv_core.so"


def library_path() -> Path:
    return _NATIVE_DIR / _library_name()


def is_available() -> bool:
    return library_path().exists()


class NativeUnavailable(RuntimeError):
    pass


_lib = None


def _load():
    global _lib
    if _lib is not None:
        return _lib

    path = library_path()
    if not path.exists():
        raise NativeUnavailable(
            f"找不到动态库 {path}\n请先运行：python tools/build_native.py"
        )

    lib = ctypes.CDLL(str(path))
    f32p = ctypes.POINTER(ctypes.c_float)

    lib.svx_create.argtypes = [ctypes.c_float]
    lib.svx_create.restype = ctypes.c_void_p

    lib.svx_destroy.argtypes = [ctypes.c_void_p]
    lib.svx_destroy.restype = None

    lib.svx_set_knobs.argtypes = [ctypes.c_void_p, ctypes.c_float,
                                  ctypes.c_float, ctypes.c_float]
    lib.svx_set_knobs.restype = None

    lib.svx_set_options.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int]
    lib.svx_set_options.restype = None

    lib.svx_reset.argtypes = [ctypes.c_void_p]
    lib.svx_reset.restype = None

    lib.svx_process.argtypes = [ctypes.c_void_p, f32p, f32p, f32p,
                                ctypes.c_uint]
    lib.svx_process.restype = None

    lib.svx_reduction_db.argtypes = [ctypes.c_void_p]
    lib.svx_reduction_db.restype = ctypes.c_float

    lib.svx_get_coeffs.argtypes = [ctypes.c_void_p, ctypes.c_int, f32p,
                                   ctypes.c_uint]
    lib.svx_get_coeffs.restype = ctypes.c_uint

    lib.svx_get_info.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.svx_get_info.restype = ctypes.c_float

    _lib = lib
    return lib


def _ptr(a: np.ndarray):
    return a.ctypes.data_as(ctypes.POINTER(ctypes.c_float))


class NativeChain:
    """与 SelfVoiceChain 用途相同，但由固件 C 代码驱动，可用于实时处理。"""

    def __init__(self, fs: float = 48000.0) -> None:
        self._lib = _load()
        self._h = self._lib.svx_create(ctypes.c_float(fs))
        if not self._h:
            raise NativeUnavailable("svx_create 返回空指针（内存分配失败）")
        self.fs = float(fs)
        self._coeff_buf = np.zeros(_MAX_COEFF_FLOATS, dtype=np.float32)

    def close(self) -> None:
        if getattr(self, "_h", None):
            self._lib.svx_destroy(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- 参数 -------------------------------------------------------------

    def set_knobs(self, mix: float, tone: float, output: float) -> None:
        self._lib.svx_set_knobs(self._h, ctypes.c_float(mix),
                                ctypes.c_float(tone), ctypes.c_float(output))

    def set_options(self, delay_mode: str = "natural", limiter: bool = True,
                    proximity_comp: bool = False) -> None:
        self._lib.svx_set_options(
            self._h,
            1 if delay_mode == "coherent" else 0,
            1 if limiter else 0,
            1 if proximity_comp else 0,
        )

    def reset(self) -> None:
        self._lib.svx_reset(self._h)

    # -- 处理 -------------------------------------------------------------

    def process(self, bone, air) -> np.ndarray:
        b = np.ascontiguousarray(bone, dtype=np.float32)
        a = np.ascontiguousarray(air, dtype=np.float32)
        if b.shape != a.shape:
            raise ValueError(f"骨导与气导长度不一致: {b.shape} vs {a.shape}")
        out = np.empty_like(b)
        self._lib.svx_process(self._h, _ptr(b), _ptr(a), _ptr(out), b.size)
        return out

    # -- 观测 -------------------------------------------------------------

    @property
    def reduction_db(self) -> float:
        return float(self._lib.svx_reduction_db(self._h))

    def coeffs(self, path: str) -> np.ndarray:
        """读回滤波器系数，供绘制频响曲线。path 取 'bone' 或 'air'。"""
        idx = 1 if path == "air" else 0
        stages = self._lib.svx_get_coeffs(self._h, idx, _ptr(self._coeff_buf),
                                          _MAX_COEFF_FLOATS)
        return self._coeff_buf[: stages * 5].copy()

    def info(self, idx: int) -> float:
        return float(self._lib.svx_get_info(self._h, idx))

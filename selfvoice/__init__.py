"""自声还原设备 —— DSP 参考实现。

这是与 STM32 固件结构一致的 Python 实现，用途有三：

1. 在硬件到货前就能把处理链跑通、把参数调准；
2. 评估套件到手后，直接处理实录的双通道信号做主观 A/B；
3. 把调好的系数导出为固件可直接编译的 C 头文件。

典型用法::

    from selfvoice import SelfVoiceChain, ChainParams

    params = ChainParams()
    params.bone_ratio = 0.6
    chain = SelfVoiceChain(params)
    out = chain.process(bone_signal, air_signal)
"""

from .biquad import BiquadCascade, cascade_response_db
from .chain import DelayLine, PeakLimiter, SelfVoiceChain
from .params import (
    AirPath,
    BonePath,
    ChainParams,
    Limiter,
    apply_mix_knob,
    apply_output_knob,
    apply_tone_knob,
)
from .wavio import read_wav, write_wav

__all__ = [
    "BiquadCascade",
    "cascade_response_db",
    "SelfVoiceChain",
    "DelayLine",
    "PeakLimiter",
    "ChainParams",
    "BonePath",
    "AirPath",
    "Limiter",
    "apply_mix_knob",
    "apply_tone_knob",
    "apply_output_knob",
    "read_wav",
    "write_wav",
]

__version__ = "0.1.0"

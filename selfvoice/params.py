"""处理链参数定义与旋钮映射。

这里的默认值是**待验证的起点**，不是结论。阶段 0（评估套件 + 实录）的
全部意义就是把这些数字调准。每个默认值下面都注明了它的来源，便于判断
哪些是有依据的、哪些纯属占位。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SPEED_OF_SOUND = 343.0  # m/s，20°C 干燥空气


# ---------------------------------------------------------------------------
# 时序：一个容易搞错的关键量
# ---------------------------------------------------------------------------
#
# 早期讨论里曾按"麦克风距嘴 20–30 cm"估算出约 0.8 ms 的路径差，但方案最终
# 确定的是**头戴麦，嘴角侧面 2–3 cm**，那个估算高了一个数量级。
#
# 更重要的是，"该延迟哪一路"取决于目标是什么，这里有两种解释：
#
#   natural（默认）—— 复现本人的自然听觉时序。
#       本人体验中，骨导几乎瞬时到达耳蜗，而气导声要绕过面部传到自己耳道
#       （约 17 cm ≈ 495 µs）。我们的麦克风只在嘴外 2.5 cm（≈73 µs）就
#       拾到了声音，**比耳朵早**。因此要把气导路延迟约 420 µs 才忠实。
#
#   coherent —— 两路严格对齐求和，最小化梳状滤波。
#       只补偿传感器之间的实际路径差（约 73 µs），声音更"干净"但不如
#       natural 忠实。
#
# 哪个更好听是听感问题，阶段 0 用耳朵判定。代码两种都支持。

MOUTH_TO_MIC_M = 0.025   # 嘴到头戴麦的距离
MOUTH_TO_EAR_M = 0.17    # 嘴到本人耳道的绕行距离（近似）


@dataclass
class BonePath:
    """骨导通路参数。"""

    # V2S200D 输出比标准 -26 dBFS 的 PDM 麦低约 20 dB，需补增益对齐。
    # 依据：V2S200D 用户指南。
    gain_db: float = 20.0

    # 高通：滤除呼吸声、皮肤摩擦、佩戴晃动。80 Hz 为经验起点。
    hp_hz: float = 80.0
    hp_order: int = 2

    # 补偿 EQ：拟合个人颅骨传递函数。**这是最需要实测标定的部分**，
    # 下面的值只是让链路跑起来的占位，没有个体依据。
    # 每项为 (中心频率 Hz, Q, 增益 dB)
    eq: list[tuple[float, float, float]] = field(
        default_factory=lambda: [
            (300.0, 0.8, 3.0),    # 中低频"厚度"
            (1200.0, 1.0, -2.0),  # 抑制皮肤共振带来的浑浊感
        ]
    )

    # 低通：皮肤对 4 kHz 以上骨导声衰减严重，同时减轻高频梳状效应。
    lp_hz: float = 3000.0
    lp_order: int = 2


@dataclass
class AirPath:
    """气导通路参数。"""

    gain_db: float = 0.0

    # 嘴 → 耳 补偿：把嘴边采到的信号修正为"到达本人耳朵"的形态。
    # 依据：自声传递函数研究显示耳侧信号相对嘴侧呈现低频抬升
    # （约 1 kHz 以下）与高频衰减（约 2 kHz 以上）。数值需实测收敛。
    mouth_to_ear_low_shelf_hz: float = 500.0
    mouth_to_ear_low_shelf_db: float = 3.0
    mouth_to_ear_high_shelf_hz: float = 4000.0
    mouth_to_ear_high_shelf_db: float = -6.0

    # 近讲效应补偿：**全指向 MEMS 麦（IM69D130）不存在近讲效应**，
    # 因此默认关闭。仅当改用定向咪头（方案 B）时才需要启用。
    proximity_comp_enabled: bool = False
    proximity_comp_hz: float = 200.0
    proximity_comp_db: float = -6.0

    # 延迟模式，见文件顶部说明
    delay_mode: str = "natural"  # "natural" | "coherent"

    def delay_seconds(self) -> float:
        if self.delay_mode == "natural":
            return (MOUTH_TO_EAR_M - MOUTH_TO_MIC_M) / SPEED_OF_SOUND
        if self.delay_mode == "coherent":
            return MOUTH_TO_MIC_M / SPEED_OF_SOUND
        raise ValueError(f"未知的 delay_mode: {self.delay_mode!r}")


@dataclass
class Limiter:
    """保护性限幅器 —— 防削波，不做音乐性压缩（那是调音台的活）。"""

    threshold_db: float = -1.0
    attack_ms: float = 1.0
    release_ms: float = 80.0
    enabled: bool = True


@dataclass
class ChainParams:
    """完整处理链参数。"""

    fs: float = 48000.0

    bone: BonePath = field(default_factory=BonePath)
    air: AirPath = field(default_factory=AirPath)
    limiter: Limiter = field(default_factory=Limiter)

    # 骨导占比 0.0–1.0。0.5 表示等比例混合。
    # 依据：Békésy 及后续研究指出说话时骨导与气导贡献大致相当，
    # 因此 0.5 是有依据的起点而非随意取值。
    bone_ratio: float = 0.5

    # 输出总增益（对应面板上可及的那个旋钮）
    output_gain_db: float = 0.0


# ---------------------------------------------------------------------------
# 旋钮映射
# ---------------------------------------------------------------------------
#
# 设计原则见方案第 09 节：不是一个参数一个旋钮。三个旋钮中有两个是宏控制，
# 沿预先设计好的曲线同时移动多个参数。

#
# 精度约定：旋钮映射的中间运算一律走 float32。
#
# 固件的参数结构体存的是 C 的 float，映射也在 float 里算；若这里用 double，
# 送进滤波器设计公式的数值会与固件有微小差异，主机对拍就永远无法逐位一致。
# 参考实现的价值就在于精确模拟固件，所以这里向固件看齐，而不是反过来。

_f32 = np.float32


def _clamp01(position: float) -> np.float32:
    return _f32(min(max(float(position), 0.0), 1.0))


def apply_mix_knob(params: ChainParams, position: float) -> ChainParams:
    """混合比旋钮：0.0 = 纯气导，1.0 = 纯骨导。

    直接映射到 bone_ratio。这个旋钮语义清晰，不需要做成宏控制。
    """
    params.bone_ratio = _clamp01(position)
    return params


def apply_tone_knob(params: ChainParams, position: float) -> ChainParams:
    """音色 / 厚度旋钮：0.0 = 薄亮，0.5 = 中性，1.0 = 厚暗。

    宏控制 —— 一个旋钮同时移动骨导补偿 EQ 的多个频点。这条曲线的形状是
    产品调音的核心，需要在阶段 1 用多人实测数据重新拟合。当前实现是线性
    插值的占位版本。
    """
    pos = _clamp01(position)
    t = _f32((pos - _f32(0.5)) * _f32(2.0))  # 映射到 -1 .. +1

    params.bone.eq = [
        # 中低频厚度：向"厚"转时提升更多
        (_f32(300.0), _f32(0.8), _f32(_f32(3.0) + _f32(3.0) * t)),
        # 浑浊带：向"厚"转时衰减减少（保留更多中频）
        (_f32(1200.0), _f32(1.0), _f32(_f32(-2.0) + _f32(1.5) * t)),
    ]
    # 向"薄亮"转时把骨导低通开高一点，让骨导路多带一些高频
    params.bone.lp_hz = _f32(_f32(3000.0) - _f32(600.0) * t)
    return params


def apply_output_knob(params: ChainParams, position: float) -> ChainParams:
    """输出电平旋钮：0.0 = -40 dB，1.0 = +12 dB，0.75 处约为 0 dB。"""
    pos = _clamp01(position)
    params.output_gain_db = _f32(_f32(-40.0) + _f32(52.0) * pos)
    return params

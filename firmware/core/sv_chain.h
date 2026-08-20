/* 自声还原处理链 —— 平台无关，主机与 STM32 共用同一份代码。
 *
 *   骨导 → 增益 → 高通 → 补偿EQ → 低通 ──────────────┐
 *                                                     ├→ 混合 → 输出增益 → 限幅
 *   气导 → 增益 → 嘴→耳补偿 → (近讲补偿) → 延迟 ─────┘
 *
 * 与 Python 参考实现 selfvoice/chain.py 逐运算对应，可在主机上对拍到逐位相同
 * （见 firmware/test）。全部缓冲区静态分配，无 malloc，无平台依赖。
 */

#ifndef SV_CHAIN_H
#define SV_CHAIN_H

#include <stdint.h>

#include "sv_biquad.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SV_BONE_MAX_STAGES   8u
#define SV_AIR_MAX_STAGES    4u
#define SV_MAX_BONE_EQ       3u
#define SV_MAX_DELAY_SAMPLES 64u  /* 48 kHz 下 1.33 ms，远超 natural 模式所需 */
#define SV_MAX_BLOCK         128u

/* 声速，20°C 干燥空气 */
#define SV_SPEED_OF_SOUND 343.0f
/* 嘴到头戴麦的距离 */
#define SV_MOUTH_TO_MIC_M 0.025f
/* 嘴到本人耳道的绕行距离（近似） */
#define SV_MOUTH_TO_EAR_M 0.17f

typedef enum {
    /* 复现本人自然听觉时序：骨导几乎瞬时到达耳蜗，气导声要绕过面部传到
     * 自己耳道，而麦克风在嘴外 2.5 cm 就拾到了，比耳朵早约 423 µs。 */
    SV_DELAY_NATURAL = 0,
    /* 只补偿两传感器的实际路径差（约 73 µs），两路严格对齐求和。 */
    SV_DELAY_COHERENT = 1
} sv_delay_mode_t;

typedef struct {
    float f0;
    float q;
    float gain_db;
} sv_eq_band_t;

typedef struct {
    float        gain_db;  /* V2S200D 电平归一化，约 +20 dB */
    float        hp_hz;
    unsigned     hp_order;
    sv_eq_band_t eq[SV_MAX_BONE_EQ];
    unsigned     eq_count;
    float        lp_hz;
    unsigned     lp_order;
} sv_bone_params_t;

typedef struct {
    float           gain_db;
    float           m2e_low_shelf_hz;
    float           m2e_low_shelf_db;
    float           m2e_high_shelf_hz;
    float           m2e_high_shelf_db;
    /* 全指向 MEMS 无近讲效应，默认关闭；仅改用定向咪头时启用 */
    int             proximity_comp_enabled;
    float           proximity_comp_hz;
    float           proximity_comp_db;
    sv_delay_mode_t delay_mode;
} sv_air_params_t;

typedef struct {
    float threshold_db;
    float attack_ms;
    float release_ms;
    int   enabled;
} sv_limiter_params_t;

typedef struct {
    float               fs;
    sv_bone_params_t    bone;
    sv_air_params_t     air;
    sv_limiter_params_t limiter;
    float               bone_ratio; /* 0 = 纯气导，1 = 纯骨导 */
    float               output_gain_db;
} sv_params_t;

typedef struct {
    sv_params_t params;

    sv_biquad_t bone_filter;
    float       bone_coeffs[SV_BONE_MAX_STAGES * SV_COEFFS_PER_STAGE];
    float       bone_state[SV_BONE_MAX_STAGES * SV_STATE_PER_STAGE];

    sv_biquad_t air_filter;
    float       air_coeffs[SV_AIR_MAX_STAGES * SV_COEFFS_PER_STAGE];
    float       air_state[SV_AIR_MAX_STAGES * SV_STATE_PER_STAGE];

    float    delay_buf[SV_MAX_DELAY_SAMPLES];
    uint32_t delay_len;
    uint32_t delay_idx;

    float lim_threshold;
    float lim_att;
    float lim_rel;
    float lim_gain;
    float lim_reduction_db; /* 最近一次处理的最大增益衰减，供面板监看 */

    float bone_gain;
    float air_gain;
    float output_gain;

    float scratch[SV_MAX_BLOCK];
} sv_chain_t;

/* 填入默认参数。默认值的依据见 Python 侧 selfvoice/params.py 的注释：
 * 部分有实测/文献依据，部分是等待阶段 0 标定的占位值。 */
void sv_params_defaults(sv_params_t *p, float fs);

/* 完整初始化：重算系数并清零状态。上电或切换用户时调用。 */
void sv_chain_init(sv_chain_t *c, const sv_params_t *p);

/* 只重算系数与增益，**不动滤波器状态**。旋钮转动时调用这个。
 *
 * 之所以要区分：清零状态会让输出出现明显咔哒声，演出中转旋钮就会响一下。
 * RBJ 是封闭形式，重算全部系数只需几微秒，可以直接在音频块间隙做。 */
void sv_chain_update_coeffs(sv_chain_t *c, const sv_params_t *p);

/* 仅清零状态，保留系数。 */
void sv_chain_reset(sv_chain_t *c);

/* 处理一块样本。n 不得超过 SV_MAX_BLOCK。
 * 状态跨调用保持，因此按 DMA 块反复调用不会产生块边界不连续。 */
void sv_chain_process(sv_chain_t *c, const float *bone, const float *air,
                      float *out, uint32_t n);

/* ---- 旋钮映射（对应面板三个旋钮，详见方案第 09 节）---------------- */

/* 混合比：0 = 纯气导，1 = 纯骨导。语义清晰，直接映射。 */
void sv_knob_mix(sv_params_t *p, float position);

/* 音色/厚度：0 = 薄亮，0.5 = 中性，1 = 厚暗。
 * 宏控制 —— 一个旋钮沿预设曲线同时移动骨导补偿 EQ 的多个频点与低通频率。
 * 曲线形状是产品调音的核心，需在阶段 1 用多人实测数据重新拟合。 */
void sv_knob_tone(sv_params_t *p, float position);

/* 输出电平：0 = −40 dB，0.75 ≈ 0 dB，1 = +12 dB。 */
void sv_knob_output(sv_params_t *p, float position);

/* 当前配置下气导路的延迟，单位秒。 */
float sv_air_delay_seconds(const sv_params_t *p);

#ifdef __cplusplus
}
#endif

#endif /* SV_CHAIN_H */

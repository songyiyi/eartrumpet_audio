#include "sv_chain.h"

#include <math.h>

#include "sv_design.h"

static float clamp01(float v)
{
    if (v < 0.0f) {
        return 0.0f;
    }
    if (v > 1.0f) {
        return 1.0f;
    }
    return v;
}

static float db_to_lin(float db)
{
    return (float)pow(10.0, (double)db / 20.0);
}

void sv_params_defaults(sv_params_t *p, float fs)
{
    p->fs = fs;

    /* 依据 V2S200D 用户指南：其输出比标准 −26 dBFS 的 PDM 麦低约 20 dB。 */
    p->bone.gain_db = 20.0f;
    p->bone.hp_hz = 80.0f;
    p->bone.hp_order = 2u;
    /* 以下两段补偿 EQ 为占位值，等待阶段 0 实测标定。 */
    p->bone.eq[0].f0 = 300.0f;
    p->bone.eq[0].q = 0.8f;
    p->bone.eq[0].gain_db = 3.0f;
    p->bone.eq[1].f0 = 1200.0f;
    p->bone.eq[1].q = 1.0f;
    p->bone.eq[1].gain_db = -2.0f;
    p->bone.eq_count = 2u;
    p->bone.lp_hz = 3000.0f;
    p->bone.lp_order = 2u;

    p->air.gain_db = 0.0f;
    p->air.m2e_low_shelf_hz = 500.0f;
    p->air.m2e_low_shelf_db = 3.0f;
    p->air.m2e_high_shelf_hz = 4000.0f;
    p->air.m2e_high_shelf_db = -6.0f;
    p->air.proximity_comp_enabled = 0;
    p->air.proximity_comp_hz = 200.0f;
    p->air.proximity_comp_db = -6.0f;
    p->air.delay_mode = SV_DELAY_NATURAL;

    p->limiter.threshold_db = -1.0f;
    p->limiter.attack_ms = 1.0f;
    p->limiter.release_ms = 80.0f;
    p->limiter.enabled = 1;

    /* Békésy 及后续研究指出说话时骨导与气导贡献大致相当，
     * 因此 0.5 是有依据的起点而非随意取值。 */
    p->bone_ratio = 0.5f;
    p->output_gain_db = 0.0f;
}

float sv_air_delay_seconds(const sv_params_t *p)
{
    if (p->air.delay_mode == SV_DELAY_COHERENT) {
        return SV_MOUTH_TO_MIC_M / SV_SPEED_OF_SOUND;
    }
    return (SV_MOUTH_TO_EAR_M - SV_MOUTH_TO_MIC_M) / SV_SPEED_OF_SOUND;
}

void sv_chain_update_coeffs(sv_chain_t *c, const sv_params_t *p)
{
    c->params = *p;

    const float fs = p->fs;

    /* ---- 骨导路：高通 → 补偿 EQ → 低通 ---- */
    unsigned n = 0u;
    n += sv_design_butterworth_highpass(&c->bone_coeffs[n * SV_COEFFS_PER_STAGE],
                                        p->bone.hp_hz, fs, p->bone.hp_order);
    for (unsigned i = 0u; i < p->bone.eq_count && i < SV_MAX_BONE_EQ; ++i) {
        if (n >= SV_BONE_MAX_STAGES) {
            break;
        }
        sv_design_peaking(&c->bone_coeffs[n * SV_COEFFS_PER_STAGE],
                          p->bone.eq[i].f0, p->bone.eq[i].q,
                          p->bone.eq[i].gain_db, fs);
        ++n;
    }
    n += sv_design_butterworth_lowpass(&c->bone_coeffs[n * SV_COEFFS_PER_STAGE],
                                       p->bone.lp_hz, fs, p->bone.lp_order);
    /* 直接绑定字段而不调用 sv_biquad_init —— 后者会清零状态，
     * 那正是本函数要避免的（见函数末尾说明）。 */
    c->bone_filter.coeffs = c->bone_coeffs;
    c->bone_filter.state = c->bone_state;
    c->bone_filter.num_stages = (uint8_t)n;

    /* ---- 气导路：嘴→耳补偿（+ 可选近讲补偿）---- */
    unsigned m = 0u;
    sv_design_low_shelf(&c->air_coeffs[m * SV_COEFFS_PER_STAGE],
                        p->air.m2e_low_shelf_hz, 0.707f,
                        p->air.m2e_low_shelf_db, fs);
    ++m;
    sv_design_high_shelf(&c->air_coeffs[m * SV_COEFFS_PER_STAGE],
                         p->air.m2e_high_shelf_hz, 0.707f,
                         p->air.m2e_high_shelf_db, fs);
    ++m;
    if (p->air.proximity_comp_enabled) {
        sv_design_low_shelf(&c->air_coeffs[m * SV_COEFFS_PER_STAGE],
                            p->air.proximity_comp_hz, 0.707f,
                            p->air.proximity_comp_db, fs);
        ++m;
    }
    c->air_filter.coeffs = c->air_coeffs;
    c->air_filter.state = c->air_state;
    c->air_filter.num_stages = (uint8_t)m;

    /* ---- 延迟线：只做整数样本延迟，固件里就是读指针偏移，零运算开销 ---- */
    double d = (double)sv_air_delay_seconds(p) * (double)fs;
    long rounded = (long)(d + 0.5);
    if (rounded < 0) {
        rounded = 0;
    }
    if ((uint32_t)rounded > SV_MAX_DELAY_SAMPLES) {
        rounded = (long)SV_MAX_DELAY_SAMPLES;
    }
    c->delay_len = (uint32_t)rounded;

    /* ---- 限幅器 ---- */
    c->lim_threshold = db_to_lin(p->limiter.threshold_db);
    {
        const double att_ms = (p->limiter.attack_ms > 1e-3f)
                                  ? (double)p->limiter.attack_ms : 1e-3;
        const double rel_ms = (p->limiter.release_ms > 1e-3f)
                                  ? (double)p->limiter.release_ms : 1e-3;
        c->lim_att = (float)exp(-1.0 / (att_ms * 1e-3 * (double)fs));
        c->lim_rel = (float)exp(-1.0 / (rel_ms * 1e-3 * (double)fs));
    }

    c->bone_gain = db_to_lin(p->bone.gain_db);
    c->air_gain = db_to_lin(p->air.gain_db);
    c->output_gain = db_to_lin(p->output_gain_db);

    /* 刻意不碰状态：旋钮转动时若清零滤波器状态，输出会有明显咔哒声。
     * 需要清零时由调用方显式调用 sv_chain_reset()。 */
}

void sv_chain_init(sv_chain_t *c, const sv_params_t *p)
{
    /* delay_len 在 update_coeffs 里算出，reset 依赖它，故顺序不可颠倒。 */
    sv_chain_update_coeffs(c, p);
    sv_chain_reset(c);
}

void sv_chain_reset(sv_chain_t *c)
{
    sv_biquad_reset(&c->bone_filter);
    sv_biquad_reset(&c->air_filter);

    for (uint32_t i = 0u; i < SV_MAX_DELAY_SAMPLES; ++i) {
        c->delay_buf[i] = 0.0f;
    }
    c->delay_idx = 0u;

    c->lim_gain = 1.0f;
    c->lim_reduction_db = 0.0f;
}

static void delay_process(sv_chain_t *c, float *buf, uint32_t n)
{
    if (c->delay_len == 0u) {
        return;
    }
    for (uint32_t i = 0u; i < n; ++i) {
        const float out = c->delay_buf[c->delay_idx];
        c->delay_buf[c->delay_idx] = buf[i];
        c->delay_idx = (c->delay_idx + 1u) % c->delay_len;
        buf[i] = out;
    }
}

static void limiter_process(sv_chain_t *c, float *buf, uint32_t n)
{
    float g = c->lim_gain;
    const float thr = c->lim_threshold;
    float min_g = 1.0f;

    for (uint32_t i = 0u; i < n; ++i) {
        const float v = buf[i];
        const float mag = fabsf(v);
        const float target = (mag > thr) ? (thr / mag) : 1.0f;
        /* 压下去时用 attack，放回来时用 release */
        const float coef = (target < g) ? c->lim_att : c->lim_rel;
        g = coef * g + (1.0f - coef) * target;
        buf[i] = v * g;
        if (g < min_g) {
            min_g = g;
        }
    }

    c->lim_gain = g;
    c->lim_reduction_db =
        20.0f * (float)log10((double)((min_g > 1e-6f) ? min_g : 1e-6f));
}

static void process_chunk(sv_chain_t *c, const float *bone, const float *air,
                          float *out, uint32_t n)
{
    /* 骨导路 → out */
    for (uint32_t i = 0u; i < n; ++i) {
        out[i] = bone[i] * c->bone_gain;
    }
    sv_biquad_process(&c->bone_filter, out, out, n);

    /* 气导路 → scratch */
    for (uint32_t i = 0u; i < n; ++i) {
        c->scratch[i] = air[i] * c->air_gain;
    }
    sv_biquad_process(&c->air_filter, c->scratch, c->scratch, n);
    delay_process(c, c->scratch, n);

    /* 混合 → 输出增益 */
    float ratio = clamp01(c->params.bone_ratio);
    const float inv = 1.0f - ratio;
    for (uint32_t i = 0u; i < n; ++i) {
        out[i] = (out[i] * ratio + c->scratch[i] * inv) * c->output_gain;
    }

    if (c->params.limiter.enabled) {
        limiter_process(c, out, n);
    }
}

void sv_chain_process(sv_chain_t *c, const float *bone, const float *air,
                      float *out, uint32_t n)
{
    /* 内部按 SV_MAX_BLOCK 分段，因此对外没有块长上限。
     *
     * 早先这里是 if (n > SV_MAX_BLOCK) n = SV_MAX_BLOCK; —— 静默丢弃超出部分。
     * 对拍测试立刻抓到了：全零输入在第 128 个样本之后出现非零输出。
     * 静默截断这类"不报错但结果错"的行为在真机上极难定位，不要再引入。 */
    uint32_t done = 0u;
    while (done < n) {
        uint32_t chunk = n - done;
        if (chunk > SV_MAX_BLOCK) {
            chunk = SV_MAX_BLOCK;
        }
        process_chunk(c, &bone[done], &air[done], &out[done], chunk);
        done += chunk;
    }
}

/* ------------------------------------------------------------------ 旋钮 */

void sv_knob_mix(sv_params_t *p, float position)
{
    p->bone_ratio = clamp01(position);
}

void sv_knob_tone(sv_params_t *p, float position)
{
    const float pos = clamp01(position);
    const float t = (pos - 0.5f) * 2.0f; /* −1 .. +1 */

    /* 中低频厚度：向"厚"转时提升更多 */
    p->bone.eq[0].f0 = 300.0f;
    p->bone.eq[0].q = 0.8f;
    p->bone.eq[0].gain_db = 3.0f + 3.0f * t;
    /* 浑浊带：向"厚"转时衰减减少，保留更多中频 */
    p->bone.eq[1].f0 = 1200.0f;
    p->bone.eq[1].q = 1.0f;
    p->bone.eq[1].gain_db = -2.0f + 1.5f * t;
    p->bone.eq_count = 2u;

    /* 向"薄亮"转时把骨导低通开高，让骨导路多带一些高频 */
    p->bone.lp_hz = 3000.0f - 600.0f * t;
}

void sv_knob_output(sv_params_t *p, float position)
{
    p->output_gain_db = -40.0f + 52.0f * clamp01(position);
}

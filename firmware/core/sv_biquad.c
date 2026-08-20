#include "sv_biquad.h"

void sv_biquad_init(sv_biquad_t *f, float *coeffs, float *state,
                    uint8_t num_stages)
{
    f->coeffs = coeffs;
    f->state = state;
    f->num_stages = num_stages;
    sv_biquad_reset(f);
}

void sv_biquad_reset(sv_biquad_t *f)
{
    const uint32_t n = (uint32_t)f->num_stages * SV_STATE_PER_STAGE;
    for (uint32_t i = 0u; i < n; ++i) {
        f->state[i] = 0.0f;
    }
}

void sv_biquad_process(sv_biquad_t *f, const float *in, float *out,
                       uint32_t n)
{
    const uint8_t stages = f->num_stages;

    for (uint32_t i = 0u; i < n; ++i) {
        float v = in[i];

        for (uint8_t s = 0u; s < stages; ++s) {
            const float *c = &f->coeffs[(uint32_t)s * SV_COEFFS_PER_STAGE];
            float *st = &f->state[(uint32_t)s * SV_STATE_PER_STAGE];

            /* 四个状态量全部先读出再回写 —— 与 Python 参考实现的
             * 元组解包次序一致，否则回写会污染尚未用到的输入。 */
            const float x1 = st[0];
            const float x2 = st[1];
            const float y1 = st[2];
            const float y2 = st[3];

            /* 求和次序（左结合）必须与 Python 完全相同，
             * 否则浮点舍入不同，逐位对拍会失败。 */
            const float y = c[0] * v + c[1] * x1 + c[2] * x2
                            + c[3] * y1 + c[4] * y2;

            st[0] = v;
            st[1] = x1;
            st[2] = y;
            st[3] = y1;

            v = y;
        }

        out[i] = v;
    }
}

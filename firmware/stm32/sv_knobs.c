#include "sv_knobs.h"

static float absf(float v)
{
    return (v < 0.0f) ? -v : v;
}

static void knob_init(sv_knob_t *k)
{
    k->smoothed = 0.0f;
    /* 故意置为不可能的值，保证首次 update 一定提交一次 */
    k->committed = -1.0f;
}

void sv_knobs_init(sv_knobs_t *k)
{
    knob_init(&k->mix);
    knob_init(&k->tone);
    knob_init(&k->output);
    k->primed = 0;
}

/* 返回非零表示该旋钮越过死区，需要提交新值 */
static int knob_step(sv_knob_t *k, uint16_t raw, int primed)
{
    const float target = (float)raw / SV_ADC_MAX;

    if (!primed) {
        /* 上电时直接跳到当前旋钮位置。若从 0 开始平滑，输出会有一段
         * 听得见的缓升 —— 演出设备开机就该是调音师设定好的状态。 */
        k->smoothed = target;
    } else {
        k->smoothed += SV_KNOB_ALPHA * (target - k->smoothed);
    }

    if (absf(k->smoothed - k->committed) > SV_KNOB_DEADBAND) {
        k->committed = k->smoothed;
        return 1;
    }
    return 0;
}

int sv_knobs_update(sv_knobs_t *k, uint16_t raw_mix, uint16_t raw_tone,
                    uint16_t raw_output, sv_params_t *params)
{
    const int primed = k->primed;
    int changed = 0;

    if (knob_step(&k->mix, raw_mix, primed)) {
        sv_knob_mix(params, k->mix.committed);
        changed = 1;
    }
    if (knob_step(&k->tone, raw_tone, primed)) {
        sv_knob_tone(params, k->tone.committed);
        changed = 1;
    }
    if (knob_step(&k->output, raw_output, primed)) {
        sv_knob_output(params, k->output.committed);
        changed = 1;
    }

    k->primed = 1;
    return changed;
}

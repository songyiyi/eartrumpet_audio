/* 主机侧动态库封装 —— 供 Python 调参界面实时调用。
 *
 * 为什么要有这一层：Python 参考实现是逐样本循环，实测只有 1.39x 实时、
 * 约 80% CPU 占用，任何 GC 暂停或界面重绘都会造成缓冲欠载爆音，无法驱动
 * 实时试听。而固件的 C 实现快得多，且**试听到的就是固件本身的代码**，
 * 比另外引入一套 scipy 滤波器更贴近真机。
 *
 * 刻意提供扁平 API（只传标量与数组，不暴露结构体），这样 ctypes 侧不必
 * 镜像 sv_params_t 的内存布局 —— 那种镜像会在结构体字段变动时静默错位，
 * 是很难查的一类 bug。
 *
 * 编译：python tools/build_native.py
 */

#include <stdlib.h>
#include <string.h>

#include "../core/sv_chain.h"

#if defined(_WIN32)
#define SVX_API __declspec(dllexport)
#else
#define SVX_API __attribute__((visibility("default")))
#endif

typedef struct {
    sv_chain_t  chain;
    sv_params_t params;
} svx_t;

SVX_API void *svx_create(float fs)
{
    svx_t *h = (svx_t *)calloc(1u, sizeof(svx_t));
    if (!h) {
        return NULL;
    }
    sv_params_defaults(&h->params, fs);
    sv_chain_init(&h->chain, &h->params);
    return h;
}

SVX_API void svx_destroy(void *handle)
{
    free(handle);
}

/* 设置三个旋钮位置（0..1）。只重算系数，不动滤波器状态 ——
 * 与固件中旋钮变动时的行为一致，避免拖滑块时听到咔哒声。 */
SVX_API void svx_set_knobs(void *handle, float mix, float tone, float output)
{
    svx_t *h = (svx_t *)handle;
    sv_knob_mix(&h->params, mix);
    sv_knob_tone(&h->params, tone);
    sv_knob_output(&h->params, output);
    sv_chain_update_coeffs(&h->chain, &h->params);
}

/* delay_mode: 0 = natural, 1 = coherent */
SVX_API void svx_set_options(void *handle, int delay_mode, int limiter_on,
                             int proximity_comp)
{
    svx_t *h = (svx_t *)handle;
    h->params.air.delay_mode =
        (delay_mode == 1) ? SV_DELAY_COHERENT : SV_DELAY_NATURAL;
    h->params.limiter.enabled = limiter_on;
    h->params.air.proximity_comp_enabled = proximity_comp;
    sv_chain_update_coeffs(&h->chain, &h->params);
}

SVX_API void svx_reset(void *handle)
{
    svx_t *h = (svx_t *)handle;
    sv_chain_reset(&h->chain);
}

SVX_API void svx_process(void *handle, const float *bone, const float *air,
                         float *out, unsigned n)
{
    svx_t *h = (svx_t *)handle;
    sv_chain_process(&h->chain, bone, air, out, (uint32_t)n);
}

/* 最近一次处理中的最大增益衰减（dB，≤0），供界面显示限幅动作。 */
SVX_API float svx_reduction_db(void *handle)
{
    return ((svx_t *)handle)->chain.lim_reduction_db;
}

/* 读回滤波器系数，供界面绘制频响曲线。
 * path: 0 = 骨导路, 1 = 气导路。返回级数，向 out 写入 级数*5 个 float。 */
SVX_API unsigned svx_get_coeffs(void *handle, int path, float *out,
                                unsigned max_floats)
{
    svx_t *h = (svx_t *)handle;
    const sv_biquad_t *f = (path == 1) ? &h->chain.air_filter
                                       : &h->chain.bone_filter;
    const unsigned need = (unsigned)f->num_stages * SV_COEFFS_PER_STAGE;
    if (need > max_floats) {
        return 0u;
    }
    memcpy(out, f->coeffs, need * sizeof(float));
    return f->num_stages;
}

/* 供界面显示的若干标量。idx 见 selfvoice/native.py 的 INFO_* 常量。 */
SVX_API float svx_get_info(void *handle, int idx)
{
    svx_t *h = (svx_t *)handle;
    switch (idx) {
    case 0: return h->params.bone_ratio;
    case 1: return h->params.output_gain_db;
    case 2: return h->params.bone.lp_hz;
    case 3: return h->params.bone.eq[0].gain_db;
    case 4: return h->params.bone.eq[1].gain_db;
    case 5: return (float)h->chain.delay_len;
    case 6: return h->params.bone.gain_db;
    default: return 0.0f;
    }
}

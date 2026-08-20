/* 双二阶（biquad）级联 —— Direct Form I。
 *
 * 系数约定与 CMSIS-DSP 的 arm_biquad_cascade_df1_f32 完全一致，每级 5 个：
 *   {b0, b1, b2, a1, a2}
 *   y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] + a1*y[n-1] + a2*y[n-2]
 * 注意 a1/a2 前为加号（教科书形式是减号），sv_design.c 已在生成时取过负号。
 *
 * 本实现与 Python 参考实现 selfvoice/biquad.py 逐运算对应，运算次序刻意保持
 * 一致，以便主机对拍时能做到逐位相同。编译时必须关闭浮点收缩
 * （-ffp-contract=off），否则编译器会把 a*b+c 融合成 FMA，结果更精确但与
 * Python 不再逐位一致。
 *
 * 不做任何动态分配：系数与状态缓冲由调用方提供。
 */

#ifndef SV_BIQUAD_H
#define SV_BIQUAD_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SV_COEFFS_PER_STAGE 5u /* b0, b1, b2, a1, a2        */
#define SV_STATE_PER_STAGE  4u /* x[n-1], x[n-2], y[n-1], y[n-2] */

typedef struct {
    float  *coeffs;     /* num_stages * SV_COEFFS_PER_STAGE，旋钮变动时可重写 */
    float  *state;      /* num_stages * SV_STATE_PER_STAGE                    */
    uint8_t num_stages;
} sv_biquad_t;

/* 绑定缓冲区并清零状态。coeffs/state 的生命周期由调用方负责。 */
void sv_biquad_init(sv_biquad_t *f, float *coeffs, float *state,
                    uint8_t num_stages);

/* 清零状态，保留系数。切换预设或静音恢复时调用。 */
void sv_biquad_reset(sv_biquad_t *f);

/* 处理 n 个样本。状态跨调用保持，因此可按 DMA 块反复调用而不产生
 * 块边界不连续。in 与 out 可以指向同一缓冲区（原地处理）。 */
void sv_biquad_process(sv_biquad_t *f, const float *in, float *out,
                       uint32_t n);

#ifdef __cplusplus
}
#endif

#endif /* SV_BIQUAD_H */

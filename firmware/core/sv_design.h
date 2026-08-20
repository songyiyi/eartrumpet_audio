/* 滤波器系数设计 —— RBJ Audio EQ Cookbook 公式。
 *
 * 与 Python 的 selfvoice/design.py 一一对应：在 double 精度下推导，最后一次性
 * 降为 float 写入系数数组（舍入点与 Python 相同，Python 侧同样是 float64 计算
 * 后转 float32）。
 *
 * 选用 RBJ 而非其它设计法的原因见方案文档：公式是封闭形式、无需迭代，
 * 在 MCU 上重算几十个系数只要几微秒，因此旋钮转动时可以实时重算，
 * 不必预存查找表。
 *
 * 每个函数向 out[5] 写入 CMSIS 约定的 {b0, b1, b2, a1, a2}。
 */

#ifndef SV_DESIGN_H
#define SV_DESIGN_H

#ifdef __cplusplus
extern "C" {
#endif

void sv_design_lowpass(float *out, float f0, float q, float fs);
void sv_design_highpass(float *out, float f0, float q, float fs);
void sv_design_peaking(float *out, float f0, float q, float gain_db, float fs);
void sv_design_low_shelf(float *out, float f0, float q, float gain_db, float fs);
void sv_design_high_shelf(float *out, float f0, float q, float gain_db, float fs);
void sv_design_bypass(float *out);

/* 巴特沃斯高通/低通，order 必须为 >=2 的偶数。
 * 向 out 写入 order/2 个二阶节（每节 5 个系数），返回实际写入的节数。
 * 各节 Q 由极点角度决定；order=2 时即为单节 Q=0.7071。 */
unsigned sv_design_butterworth_highpass(float *out, float f0, float fs,
                                        unsigned order);
unsigned sv_design_butterworth_lowpass(float *out, float f0, float fs,
                                       unsigned order);

#ifdef __cplusplus
}
#endif

#endif /* SV_DESIGN_H */

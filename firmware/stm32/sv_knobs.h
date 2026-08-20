/* 面板旋钮读取 —— ADC 采样 → 平滑 → 死区 → 参数更新。
 *
 * 对应方案第 09 节的三旋钮设计：
 *   混合比    骨导/气导比例      沉孔式，装台时设定
 *   音色/厚度 宏控制多个 EQ 频点  沉孔式，装台时设定
 *   输出电平  总增益             面板可及，随时可调
 *
 * 两个必须处理的问题：
 *
 * 1. 平滑 —— ADC 原始读数直接映射到系数会产生 zipper noise（阶梯噪声），
 *    转旋钮时听得到一格一格的跳变。这里用一阶指数平滑。
 *
 * 2. 死区 —— ADC 末位always 在抖动。若每个音频块都因此重算系数，既浪费
 *    算力，又可能让系数持续微抖动而引入调制噪声。只有当平滑值相对上次
 *    提交值的变化超过死区阈值时才真正更新参数。
 *
 * 本模块不含任何 HAL 调用，ADC 原始值由调用方传入，因此可以在主机上测试。
 */

#ifndef SV_KNOBS_H
#define SV_KNOBS_H

#include <stdint.h>

#include "../core/sv_chain.h"

#ifdef __cplusplus
extern "C" {
#endif

/* STM32 ADC 为 12 位 */
#define SV_ADC_MAX 4095.0f

/* 平滑系数：y += alpha * (x - y)。
 * 按约 10 ms 时间常数、旋钮扫描周期 1 ms 估算。值越小越平滑但越迟钝。 */
#define SV_KNOB_ALPHA 0.1f

/* 死区：归一化行程的 0.2%，约对应 12 位 ADC 的 8 个计数。
 * 需大于 ADC 本底噪声，否则起不到抑制作用。 */
#define SV_KNOB_DEADBAND 0.002f

typedef struct {
    float smoothed;  /* 平滑后的归一化位置 0..1 */
    float committed; /* 上次真正写入参数时的值 */
} sv_knob_t;

typedef struct {
    sv_knob_t mix;
    sv_knob_t tone;
    sv_knob_t output;
    int       primed; /* 首次调用时直接跳到当前位置，避免上电缓升 */
} sv_knobs_t;

void sv_knobs_init(sv_knobs_t *k);

/* 送入一组 ADC 原始读数并更新参数。
 *
 * 返回非零表示有旋钮越过死区、params 已被修改 —— 此时调用方应调用
 * sv_chain_update_coeffs()（**不是** sv_chain_init，后者会清零滤波器
 * 状态而产生咔哒声）。
 *
 * 建议在音频块之间调用，频率约 1 kHz 即可，不必每块都调。 */
int sv_knobs_update(sv_knobs_t *k, uint16_t raw_mix, uint16_t raw_tone,
                    uint16_t raw_output, sv_params_t *params);

#ifdef __cplusplus
}
#endif

#endif /* SV_KNOBS_H */

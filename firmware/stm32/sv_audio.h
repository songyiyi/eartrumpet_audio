/* 音频管线 —— DFSDM 输入 → DSP → SAI 输出。
 *
 * 本文件只负责"格式转换 + 调用 DSP 核心"这部分逻辑，不含 HAL 初始化代码
 * （那部分由 CubeMX 生成，配置要求见本目录 README.md）。这样划分的好处是
 * 这里的逻辑可以在主机上编译和测试。
 *
 * 延迟预算（对应方案第 07 节）：
 *   块长 32 @ 48 kHz  →  输入缓冲 0.67 ms + 输出缓冲 0.67 ms = 1.33 ms
 *   PDM 抽取滤波                                              ≈ 0.04–0.1 ms
 *   PCM5102A 低延迟滤波模式（FLT 引脚拉高）                    ≈ 0.08 ms
 *   ------------------------------------------------------------------
 *   合计约 1.5 ms —— 远低于音箱空气传播的 10 ms，可忽略。
 */

#ifndef SV_AUDIO_H
#define SV_AUDIO_H

#include <stdint.h>

#include "../core/sv_chain.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 每块样本数。越小延迟越低，但中断更频繁。
 * 32 是延迟与开销的折中；H750 上 DSP 占用不到 10%，余量充足。 */
#define SV_BLOCK_SAMPLES 32u

/* 24 位有符号满刻度 */
#define SV_INT24_SCALE 8388608.0f
#define SV_INT24_MAX   8388607
#define SV_INT24_MIN   (-8388608)

typedef struct {
    sv_chain_t chain;
    /* 统计信息，供面板指示或调试用 */
    uint32_t blocks_processed;
    uint32_t output_clips; /* 输出转换时发生饱和的样本数，应长期为 0 */
} sv_audio_t;

void sv_audio_init(sv_audio_t *a, const sv_params_t *p);

/* 处理一块。
 *
 * bone_raw / air_raw 为 DFSDM 输出的 32 位字。DFSDM 把 24 位结果放在
 * DFSDM_FLTxRDATAR 的高位（低 8 位是通道号等状态位），因此这里统一右移 8
 * 位取出有符号 24 位值 —— 注意必须对**有符号**类型移位以保留符号扩展。
 *
 * out24 输出 24 位有符号值，左对齐写入 32 位槽供 SAI 发送。
 *
 * n 通常等于 SV_BLOCK_SAMPLES。 */
void sv_audio_process_block(sv_audio_t *a, const int32_t *bone_raw,
                            const int32_t *air_raw, int32_t *out24,
                            uint32_t n);

#ifdef __cplusplus
}
#endif

#endif /* SV_AUDIO_H */

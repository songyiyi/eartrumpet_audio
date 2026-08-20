#include "sv_audio.h"

static float bone_buf[SV_BLOCK_SAMPLES];
static float air_buf[SV_BLOCK_SAMPLES];
static float out_buf[SV_BLOCK_SAMPLES];

void sv_audio_init(sv_audio_t *a, const sv_params_t *p)
{
    sv_chain_init(&a->chain, p);
    a->blocks_processed = 0u;
    a->output_clips = 0u;
}

void sv_audio_process_block(sv_audio_t *a, const int32_t *bone_raw,
                            const int32_t *air_raw, int32_t *out24,
                            uint32_t n)
{
    if (n > SV_BLOCK_SAMPLES) {
        n = SV_BLOCK_SAMPLES;
    }

    /* DFSDM 数据寄存器高 24 位是结果，低 8 位是状态。对有符号类型右移
     * 以获得符号扩展；对无符号类型移位会把负样本变成巨大的正数。 */
    for (uint32_t i = 0u; i < n; ++i) {
        bone_buf[i] = (float)(bone_raw[i] >> 8) / SV_INT24_SCALE;
        air_buf[i] = (float)(air_raw[i] >> 8) / SV_INT24_SCALE;
    }

    sv_chain_process(&a->chain, bone_buf, air_buf, out_buf, n);

    for (uint32_t i = 0u; i < n; ++i) {
        /* 必须饱和而不是回绕。回绕会把一次轻微过载变成满幅方波，
         * 在演出中是灾难性的爆音；限幅器已在上游把常态电平压住，
         * 这里只是最后一道防线。 */
        int32_t v = (int32_t)(out_buf[i] * SV_INT24_SCALE);
        if (v > SV_INT24_MAX) {
            v = SV_INT24_MAX;
            ++a->output_clips;
        } else if (v < SV_INT24_MIN) {
            v = SV_INT24_MIN;
            ++a->output_clips;
        }
        /* 左对齐进 32 位槽，供 SAI 以 24 位帧发送 */
        out24[i] = v << 8;
    }

    ++a->blocks_processed;
}

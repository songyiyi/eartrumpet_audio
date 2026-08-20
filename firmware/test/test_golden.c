/* 对拍验证：固件 C 实现 vs Python 参考实现，逐位比对。
 *
 * 读取 tools/gen_golden.py 生成的 golden.bin，用相同的旋钮位置构造参数，
 * 跑同一条处理链，然后要求输出的每一个 float **逐位相同**（按位比较，
 * 不设容差）。
 *
 * 为什么必须逐位而非"误差足够小"：只要允许容差，运算次序错误、精度退化、
 * 状态未跨块保持这类问题都可能藏在容差之下，等到真机上才以杂音或周期性
 * 咔哒的形式暴露出来，而那时定位成本高得多。逐位相同是唯一能一次性排除
 * 整类问题的判据。
 *
 * 编译时必须带 -ffp-contract=off，否则编译器会把 a*b+c 融合成 FMA 指令，
 * 结果更精确但与 Python 不再逐位一致。
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../core/sv_chain.h"

typedef struct {
    float    fs;
    float    mix;
    float    tone;
    float    output;
    uint32_t delay_mode;
    uint32_t limiter_enabled;
    uint32_t n;
    uint32_t block;
    float   *bone;
    float   *air;
    float   *expected;
} case_t;

static int read_u32(FILE *f, uint32_t *v)
{
    return fread(v, sizeof(uint32_t), 1, f) == 1;
}

static int read_f32(FILE *f, float *v)
{
    return fread(v, sizeof(float), 1, f) == 1;
}

/* 按位比较：直接比 IEEE-754 位模式，不设任何容差。 */
static int bits_equal(float a, float b)
{
    uint32_t ba, bb;
    memcpy(&ba, &a, sizeof(ba));
    memcpy(&bb, &b, sizeof(bb));
    return ba == bb;
}

static sv_chain_t g_chain;
static float       g_out[1 << 16];

int main(int argc, char **argv)
{
    const char *path = (argc > 1) ? argv[1] : "firmware/test/golden.bin";

    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "无法打开黄金向量文件: %s\n", path);
        fprintf(stderr, "请先运行: python tools/gen_golden.py\n");
        return 2;
    }

    char magic[4];
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, "SVGV", 4) != 0) {
        fprintf(stderr, "文件格式错误：magic 不匹配\n");
        fclose(f);
        return 2;
    }

    uint32_t version = 0u, n_cases = 0u;
    if (!read_u32(f, &version) || !read_u32(f, &n_cases) || version != 1u) {
        fprintf(stderr, "不支持的黄金向量版本: %u\n", version);
        fclose(f);
        return 2;
    }

    printf("黄金向量 %s —— %u 个用例\n\n", path, n_cases);

    unsigned failed = 0u;

    for (uint32_t ci = 0u; ci < n_cases; ++ci) {
        case_t c;
        memset(&c, 0, sizeof(c));

        if (!read_f32(f, &c.fs) || !read_f32(f, &c.mix) ||
            !read_f32(f, &c.tone) || !read_f32(f, &c.output) ||
            !read_u32(f, &c.delay_mode) || !read_u32(f, &c.limiter_enabled) ||
            !read_u32(f, &c.n) || !read_u32(f, &c.block)) {
            fprintf(stderr, "用例 %u 头部读取失败\n", ci);
            fclose(f);
            return 2;
        }

        if (c.n > (sizeof(g_out) / sizeof(g_out[0]))) {
            fprintf(stderr, "用例 %u 样本数 %u 超出输出缓冲\n", ci, c.n);
            fclose(f);
            return 2;
        }

        c.bone = malloc(c.n * sizeof(float));
        c.air = malloc(c.n * sizeof(float));
        c.expected = malloc(c.n * sizeof(float));
        if (!c.bone || !c.air || !c.expected) {
            fprintf(stderr, "内存分配失败\n");
            fclose(f);
            return 2;
        }
        if (fread(c.bone, sizeof(float), c.n, f) != c.n ||
            fread(c.air, sizeof(float), c.n, f) != c.n ||
            fread(c.expected, sizeof(float), c.n, f) != c.n) {
            fprintf(stderr, "用例 %u 数据读取失败\n", ci);
            fclose(f);
            return 2;
        }

        /* 用与 Python 相同的旋钮位置派生参数 —— 参数派生逻辑也纳入对拍 */
        sv_params_t p;
        sv_params_defaults(&p, c.fs);
        sv_knob_mix(&p, c.mix);
        sv_knob_tone(&p, c.tone);
        sv_knob_output(&p, c.output);
        p.air.delay_mode = (c.delay_mode == 1u) ? SV_DELAY_COHERENT
                                                : SV_DELAY_NATURAL;
        p.limiter.enabled = (int)c.limiter_enabled;

        sv_chain_init(&g_chain, &p);

        if (c.block == 0u) {
            sv_chain_process(&g_chain, c.bone, c.air, g_out, c.n);
        } else {
            for (uint32_t i = 0u; i < c.n; i += c.block) {
                uint32_t len = c.block;
                if (i + len > c.n) {
                    len = c.n - i;
                }
                sv_chain_process(&g_chain, &c.bone[i], &c.air[i],
                                 &g_out[i], len);
            }
        }

        uint32_t mismatches = 0u;
        uint32_t first_bad = 0u;
        float    worst = 0.0f;
        for (uint32_t i = 0u; i < c.n; ++i) {
            if (!bits_equal(g_out[i], c.expected[i])) {
                if (mismatches == 0u) {
                    first_bad = i;
                }
                ++mismatches;
                float d = g_out[i] - c.expected[i];
                if (d < 0.0f) {
                    d = -d;
                }
                if (d > worst) {
                    worst = d;
                }
            }
        }

        if (mismatches == 0u) {
            printf("  ok    用例 %-2u  n=%-5u block=%-4u 逐位一致\n",
                   ci, c.n, c.block);
        } else {
            ++failed;
            printf("  FAIL  用例 %-2u  n=%-5u block=%-4u  %u/%u 个样本不符\n",
                   ci, c.n, c.block, mismatches, c.n);
            printf("          首个差异 @%u:  C=%.9g  Python=%.9g\n",
                   first_bad, (double)g_out[first_bad],
                   (double)c.expected[first_bad]);
            printf("          最大绝对差: %.3e\n", (double)worst);
        }

        free(c.bone);
        free(c.air);
        free(c.expected);
    }

    fclose(f);

    printf("\n");
    if (failed) {
        printf("%u / %u 个用例失败\n", failed, n_cases);
        return 1;
    }
    printf("全部 %u 个用例逐位一致 —— C 实现与 Python 参考实现数值等价\n",
           n_cases);
    return 0;
}

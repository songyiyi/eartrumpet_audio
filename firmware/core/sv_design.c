#include "sv_design.h"

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* 归一化到 a0=1，并转为 CMSIS 约定（a1/a2 取负）。
 * 全程 double，仅在写入 out 时降为 float —— 与 Python 的舍入点一致。 */
static void finalize(float *out, double b0, double b1, double b2,
                     double a0, double a1, double a2)
{
    out[0] = (float)(b0 / a0);
    out[1] = (float)(b1 / a0);
    out[2] = (float)(b2 / a0);
    out[3] = (float)(-a1 / a0);
    out[4] = (float)(-a2 / a0);
}

void sv_design_lowpass(float *out, float f0, float q, float fs)
{
    const double w0 = 2.0 * M_PI * (double)f0 / (double)fs;
    const double cos_w0 = cos(w0);
    const double alpha = sin(w0) / (2.0 * (double)q);

    const double b0 = (1.0 - cos_w0) / 2.0;
    finalize(out, b0, 1.0 - cos_w0, b0,
             1.0 + alpha, -2.0 * cos_w0, 1.0 - alpha);
}

void sv_design_highpass(float *out, float f0, float q, float fs)
{
    const double w0 = 2.0 * M_PI * (double)f0 / (double)fs;
    const double cos_w0 = cos(w0);
    const double alpha = sin(w0) / (2.0 * (double)q);

    const double b0 = (1.0 + cos_w0) / 2.0;
    finalize(out, b0, -(1.0 + cos_w0), b0,
             1.0 + alpha, -2.0 * cos_w0, 1.0 - alpha);
}

void sv_design_peaking(float *out, float f0, float q, float gain_db, float fs)
{
    const double w0 = 2.0 * M_PI * (double)f0 / (double)fs;
    const double cos_w0 = cos(w0);
    const double A = pow(10.0, (double)gain_db / 40.0);
    const double alpha = sin(w0) / (2.0 * (double)q);

    finalize(out, 1.0 + alpha * A, -2.0 * cos_w0, 1.0 - alpha * A,
             1.0 + alpha / A, -2.0 * cos_w0, 1.0 - alpha / A);
}

void sv_design_low_shelf(float *out, float f0, float q, float gain_db, float fs)
{
    const double w0 = 2.0 * M_PI * (double)f0 / (double)fs;
    const double cos_w0 = cos(w0);
    const double A = pow(10.0, (double)gain_db / 40.0);
    const double alpha = sin(w0) / (2.0 * (double)q);
    const double tsa = 2.0 * sqrt(A) * alpha;

    finalize(out,
             A * ((A + 1.0) - (A - 1.0) * cos_w0 + tsa),
             2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0),
             A * ((A + 1.0) - (A - 1.0) * cos_w0 - tsa),
             (A + 1.0) + (A - 1.0) * cos_w0 + tsa,
             -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0),
             (A + 1.0) + (A - 1.0) * cos_w0 - tsa);
}

void sv_design_high_shelf(float *out, float f0, float q, float gain_db, float fs)
{
    const double w0 = 2.0 * M_PI * (double)f0 / (double)fs;
    const double cos_w0 = cos(w0);
    const double A = pow(10.0, (double)gain_db / 40.0);
    const double alpha = sin(w0) / (2.0 * (double)q);
    const double tsa = 2.0 * sqrt(A) * alpha;

    finalize(out,
             A * ((A + 1.0) + (A - 1.0) * cos_w0 + tsa),
             -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0),
             A * ((A + 1.0) + (A - 1.0) * cos_w0 - tsa),
             (A + 1.0) - (A - 1.0) * cos_w0 + tsa,
             2.0 * ((A - 1.0) - (A + 1.0) * cos_w0),
             (A + 1.0) - (A - 1.0) * cos_w0 - tsa);
}

void sv_design_bypass(float *out)
{
    out[0] = 1.0f;
    out[1] = 0.0f;
    out[2] = 0.0f;
    out[3] = 0.0f;
    out[4] = 0.0f;
}

static unsigned butterworth(float *out, float f0, float fs, unsigned order,
                            int highpass)
{
    if ((order < 2u) || ((order % 2u) != 0u)) {
        return 0u;
    }

    const unsigned sections = order / 2u;
    for (unsigned k = 0u; k < sections; ++k) {
        const double theta =
            M_PI * (2.0 * (double)k + 1.0) / (2.0 * (double)order);
        const float q = (float)(1.0 / (2.0 * sin(theta)));

        if (highpass) {
            sv_design_highpass(&out[k * 5u], f0, q, fs);
        } else {
            sv_design_lowpass(&out[k * 5u], f0, q, fs);
        }
    }
    return sections;
}

unsigned sv_design_butterworth_highpass(float *out, float f0, float fs,
                                        unsigned order)
{
    return butterworth(out, f0, fs, order, 1);
}

unsigned sv_design_butterworth_lowpass(float *out, float f0, float fs,
                                       unsigned order)
{
    return butterworth(out, f0, fs, order, 0);
}

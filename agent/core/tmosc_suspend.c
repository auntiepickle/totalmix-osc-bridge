/* tmosc_suspend.c — see tmosc_suspend.h. */
#include "tmosc_suspend.h"

void tm_suspend_init(tm_suspend *s)
{
    s->biased = 0.0;
    s->unbiased = 0.0;
    s->primed = 0;
}

double tm_suspend_check(tm_suspend *s, double biased_ms, double unbiased_ms,
                        double threshold_ms)
{
    double slept;

    if (!s->primed) {
        s->biased = biased_ms;
        s->unbiased = unbiased_ms;
        s->primed = 1;
        return 0.0;
    }

    slept = (biased_ms - s->biased) - (unbiased_ms - s->unbiased);
    s->biased = biased_ms;
    s->unbiased = unbiased_ms;

    /* Below the threshold is ordinary drift between two clock reads, and the
     * comparison also discards the negative side of that jitter. */
    if (slept < threshold_ms) return 0.0;
    return slept;
}

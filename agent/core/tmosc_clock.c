/* tmosc_clock.c — see tmosc_clock.h. */
#include "tmosc_clock.h"

void tm_clock_init(tm_clock *c)
{
    c->count = 0;
    c->bpm = 0;
}

int tm_clock_tick(tm_clock *c, double now_ms)
{
    int i;
    double sum, avg, bpm;

    if (c->count < TM_CLOCK_WINDOW) {
        c->ticks[c->count++] = now_ms;
    } else {
        for (i = 1; i < TM_CLOCK_WINDOW; i++) c->ticks[i - 1] = c->ticks[i];
        c->ticks[TM_CLOCK_WINDOW - 1] = now_ms;
    }

    if (c->count < 4) return 0;

    sum = 0.0;
    for (i = 1; i < c->count; i++) sum += c->ticks[i] - c->ticks[i - 1];
    avg = sum / (double)(c->count - 1);
    if (avg <= 0.0) return 0;

    /* round(60000 / (24 * avg)) */
    bpm = 60000.0 / (24.0 * avg);
    {
        int r = (int)(bpm + 0.5);
        if (r < 20 || r > 400) return 0;
        c->bpm = r;
        return r;
    }
}

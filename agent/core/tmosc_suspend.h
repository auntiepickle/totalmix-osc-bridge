/* tmosc_suspend.h — "was the machine asleep?" detector (freestanding base).
 *
 * A USB MIDI handle rarely survives a system suspend. The device is
 * re-enumerated on resume while the open handle keeps looking perfectly
 * valid: it reports no error, it is still exclusively allocated to us, and it
 * simply never delivers another byte. Nothing in the MIDI API says so, so the
 * only cure is to reopen on resume — which means noticing the resume.
 *
 * Two clocks tell you, with no notification API and no platform headers: one
 * that keeps counting while the machine is suspended (Windows GetTickCount64,
 * POSIX CLOCK_BOOTTIME) and one that does not (Windows
 * QueryUnbiasedInterruptTime, POSIX CLOCK_MONOTONIC). Poll them together;
 * when the first advances further than the second, the difference is exactly
 * the time spent asleep.
 */
#ifndef TMOSC_SUSPEND_H
#define TMOSC_SUSPEND_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double biased;     /* last sample of the suspend-counting clock (ms) */
    double unbiased;   /* last sample of the suspend-excluding clock (ms) */
    int    primed;     /* 0 until the first sample lands */
} tm_suspend;

void tm_suspend_init(tm_suspend *s);

/* Sample both clocks. Returns how many milliseconds the machine spent
 * suspended since the previous call, when that exceeds threshold_ms; else 0.
 * The first call only primes the state and always returns 0, so a freshly
 * opened port is never reported stale. threshold_ms must be > 0: it also
 * absorbs the sampling jitter between the two clock reads. */
double tm_suspend_check(tm_suspend *s, double biased_ms, double unbiased_ms,
                        double threshold_ms);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_SUSPEND_H */

/* tmosc_clock.h — MIDI clock -> BPM (freestanding base).
 *
 * Port of midi.js _processMIDIClock: 24 PPQN timing clocks (0xF8), average the
 * interval over a sliding window, clamp to 20..400 BPM. Pure: you feed it a
 * monotonic millisecond timestamp per clock tick.
 */
#ifndef TMOSC_CLOCK_H
#define TMOSC_CLOCK_H

#ifdef __cplusplus
extern "C" {
#endif

#ifndef TM_CLOCK_WINDOW
#define TM_CLOCK_WINDOW 25    /* keep up to N recent tick timestamps */
#endif

typedef struct {
    double ticks[TM_CLOCK_WINDOW];
    int count;
    int bpm;                  /* last valid BPM, or 0 */
} tm_clock;

void tm_clock_init(tm_clock *c);

/* Record a timing-clock tick at now_ms. Returns the current BPM (>0) when a
 * fresh valid value is available this tick, else 0. c->bpm always holds the
 * last valid BPM. */
int tm_clock_tick(tm_clock *c, double now_ms);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_CLOCK_H */

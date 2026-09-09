/* runner.h — the platform-agnostic agent loop.
 *
 * The OS backends (ALSA, WinMM) differ only in how MIDI arrives; everything
 * else — connect, fetch the bindings, match, coalesce knobs, POST the bridge,
 * refresh, reconnect — is shared here and driven through a small MIDI-source
 * interface. This is what keeps one dispatch implementation across platforms
 * (and is the seam a microcontroller port would also plug into).
 */
#ifndef TM_RUNNER_H
#define TM_RUNNER_H

#include "tmosc_midi.h"   /* tm_midi_msg */

/* A MIDI source the runner pulls from. Both callbacks take the backend ctx. */
typedef struct {
    /* Drain up to `max` decoded messages into out[]. Returns the count (>=0),
     * or -1 on a fatal source error (runner exits). */
    int (*read)(void *ctx, tm_midi_msg *out, int max);
    /* Block up to `ms` waiting for input (may return early). */
    void (*wait)(void *ctx, int ms);
} tm_midi_src;

/* Run until stopped. host/port = bridge. verbose logs each dispatched action.
 * Returns 0 on clean stop, non-zero on unrecoverable error. */
int tm_runner(const char *host, int port, const tm_midi_src *src, void *ctx, int verbose);

/* Connect, fetch + print the trigger table, return. No MIDI, no writes. */
int tm_runner_dryrun(const char *host, int port);

/* Ask the runner loop to stop (signal handler / tray Quit). */
void tm_runner_stop(void);

#endif /* TM_RUNNER_H */

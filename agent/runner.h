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
    /* Optional (set to NULL when the backend has no such failure mode).
     * Polled every couple of seconds: return 0 while the port is live, -1
     * once it has silently gone stale — the classic case is a handle that
     * outlived a system suspend, which reports no error and delivers nothing
     * ever again. The runner then exits with a device error so the caller
     * closes the port and opens it afresh. */
    int (*health)(void *ctx);
} tm_midi_src;

/* Run until stopped. host/port = bridge. verbose logs each dispatched action.
 * Returns 0 on clean stop, non-zero on unrecoverable error. */
int tm_runner(const char *host, int port, const tm_midi_src *src, void *ctx, int verbose);

/* Connect, fetch + print the trigger table, return. No MIDI, no writes. */
int tm_runner_dryrun(const char *host, int port);

/* Ask the runner loop to stop (signal handler / tray Quit). */
void tm_runner_stop(void);

/* Optional: called from the runner's thread whenever the bridge link changes
 * (1 = connected + bindings loaded, 0 = connect failed / dropped). Fires on
 * change only. NULL (default) = no callback. A tray uses it to show a
 * "bridge not found" state instead of a stale green icon. */
void tm_runner_set_link_callback(void (*cb)(int connected));

/* Optional: provider of the TotalMix main-window title on this machine
 * (the Windows console + tray set it; a daemon on a box without TotalMix
 * leaves it NULL). Called before every heartbeat: write the UTF-8 title into
 * buf (cap bytes, NUL-terminated) and return its length, 0 when no TotalMix
 * window exists. The heartbeat then carries "title" and the bridge adopts
 * the workspace TotalMix shows (#30). NULL (default) = field not sent. */
void tm_runner_set_title_provider(int (*cb)(char *buf, int cap));

/* One-shot: connect and POST a single MIDI-ownership heartbeat, then close.
 * Used while the MIDI device is busy/unavailable so the bridge still sees the
 * agent present and a browser yields Web MIDI (closing the port), letting the
 * next open succeed. Returns 0 on success, non-zero if the bridge is
 * unreachable. */
int tm_runner_announce(const char *host, int port);

#endif /* TM_RUNNER_H */

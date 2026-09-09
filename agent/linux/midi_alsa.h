/* midi_alsa.h — ALSA rawmidi input backend (Linux). Opens a MIDI input port,
 * exposes its poll fd so the daemon can wait on MIDI + a flush timer together,
 * and hands raw bytes to the portable parser. */
#ifndef TM_MIDI_ALSA_H
#define TM_MIDI_ALSA_H

#include <poll.h>
#include "midi_port.h"

typedef struct tm_midi_alsa tm_midi_alsa;

/* Enumerate rawmidi input ports into `out` (up to `max`). Returns the count. */
int tm_midi_alsa_list(tm_midi_port *out, int max);

/* Resolve a device query to an ALSA port id in `out`:
 *   - "hw:X,Y" (starts with "hw:") is used verbatim
 *   - otherwise a case-insensitive SUBSTRING of a port's friendly name
 *     (e.g. "U6MIDI"); empty/NULL picks the first input port
 * Returns 0 on success, -1 if nothing matched. */
int tm_midi_alsa_resolve(const char *query, char *out, int outlen);

/* Open the rawmidi input `device` (an ALSA id like "hw:1,0"). NULL. */
tm_midi_alsa *tm_midi_alsa_open(const char *device);
void tm_midi_alsa_close(tm_midi_alsa *m);

/* Fill pollfds for waiting on incoming MIDI. Returns count filled (<= max). */
int tm_midi_alsa_pollfds(tm_midi_alsa *m, struct pollfd *pfds, int max);

/* Read available bytes into buf (up to cap). Returns count, 0 if none ready,
 * -1 on error. Non-blocking. */
int tm_midi_alsa_read(tm_midi_alsa *m, unsigned char *buf, int cap);

#endif /* TM_MIDI_ALSA_H */

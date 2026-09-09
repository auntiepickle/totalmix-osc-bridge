/* midi_alsa.h — ALSA rawmidi input backend (Linux). Opens a MIDI input port,
 * exposes its poll fd so the daemon can wait on MIDI + a flush timer together,
 * and hands raw bytes to the portable parser. */
#ifndef TM_MIDI_ALSA_H
#define TM_MIDI_ALSA_H

#include <poll.h>

typedef struct tm_midi_alsa tm_midi_alsa;

/* Open the rawmidi input `device` (e.g. "hw:1,0" — find it with `amidi -l`).
 * Returns a handle or NULL. */
tm_midi_alsa *tm_midi_alsa_open(const char *device);
void tm_midi_alsa_close(tm_midi_alsa *m);

/* Fill pollfds for waiting on incoming MIDI. Returns count filled (<= max). */
int tm_midi_alsa_pollfds(tm_midi_alsa *m, struct pollfd *pfds, int max);

/* Read available bytes into buf (up to cap). Returns count, 0 if none ready,
 * -1 on error. Non-blocking. */
int tm_midi_alsa_read(tm_midi_alsa *m, unsigned char *buf, int cap);

#endif /* TM_MIDI_ALSA_H */

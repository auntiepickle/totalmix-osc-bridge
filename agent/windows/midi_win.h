/* midi_win.h — WinMM rawmidi input backend (Windows). Decodes short MIDI
 * messages in the WinMM callback thread into a small ring the runner drains. */
#ifndef TM_MIDI_WIN_H
#define TM_MIDI_WIN_H

#include "tmosc_midi.h"
#include "midi_port.h"

typedef struct tm_midi_win tm_midi_win;

/* Enumerate MIDI input devices. `port` is the device index as text. */
int tm_midi_win_list(tm_midi_port *out, int max);

/* Resolve a query to a device index string in `out`:
 *   - all-digits -> that device index
 *   - otherwise a case-insensitive name substring; empty/NULL -> device 0 */
int tm_midi_win_resolve(const char *query, char *out, int outlen);

/* Open by device-index string (from resolve). Returns handle or NULL. */
tm_midi_win *tm_midi_win_open(const char *index_str);
void tm_midi_win_close(tm_midi_win *m);

/* Drain queued decoded messages into out (up to max). Returns count. */
int tm_midi_win_read(tm_midi_win *m, tm_midi_msg *out, int max);

/* Wait up to ms for input (returns early when a message arrives). */
void tm_midi_win_wait(tm_midi_win *m, int ms);

#endif /* TM_MIDI_WIN_H */

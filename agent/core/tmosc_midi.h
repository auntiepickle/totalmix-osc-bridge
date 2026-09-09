/* tmosc_midi.h — portable MIDI byte-stream parser (the freestanding base).
 *
 * Zero dependencies, C99, no malloc, no platform headers. Turns a raw MIDI
 * byte stream (as an ALSA rawmidi read, a UART, or a USB-MIDI host on a
 * microcontroller delivers) into discrete channel-voice / realtime messages,
 * handling MIDI running status and skipping System Exclusive.
 *
 * Desktop backends built on a parsing MIDI library (PortMidi, WinMM, CoreMIDI)
 * already deliver discrete 3-byte messages; those can skip this parser and feed
 * tm_midi_msg straight to the matcher. This parser exists for the raw-stream
 * transports (rawmidi / embedded), which is where portability actually bites.
 */
#ifndef TMOSC_MIDI_H
#define TMOSC_MIDI_H

#ifdef __cplusplus
extern "C" {
#endif

/* One decoded MIDI message. `status` is the FULL status byte (type | channel);
 * data2 is 0 for one-data-byte messages (Program Change, Channel Aftertouch)
 * and for realtime bytes (e.g. 0xF8 Timing Clock). */
typedef struct {
    int status;
    int data1;
    int data2;
} tm_midi_msg;

/* Streaming parser state. Initialize with tm_midi_parser_init, then push bytes
 * one at a time. */
typedef struct {
    int running_status;   /* last channel-voice status byte, or 0 */
    int expected;         /* data bytes expected for running_status: 0/1/2 */
    int data0;            /* first data byte held while awaiting the second */
    int have_data0;
    int in_sysex;
} tm_midi_parser;

void tm_midi_parser_init(tm_midi_parser *p);

/* Push a single raw MIDI byte. When a complete message is ready, fills *out
 * and returns 1; otherwise returns 0. Realtime bytes (>= 0xF8) emit
 * immediately as {status=byte, 0, 0} and never disturb running status. */
int tm_midi_parser_push(tm_midi_parser *p, unsigned char byte, tm_midi_msg *out);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_MIDI_H */

/* midi_port.h — a discovered MIDI input port (shared by the backends). */
#ifndef TM_MIDI_PORT_H
#define TM_MIDI_PORT_H

typedef struct {
    char port[24];    /* backend id: ALSA "hw:1,0" or WinMM device index */
    char name[80];    /* friendly device name */
} tm_midi_port;

#endif

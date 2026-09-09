/* tmosc_match.h — portable MIDI trigger matcher (freestanding base).
 *
 * A faithful C port of the browser's midi.js matching: a decoded MIDI message
 * in, zero or more ACTIONS out (fire a macro, or stream a knob value 0..1).
 * No allocation, no strings — macros are referenced by their index in the
 * caller's array, and the platform layer maps that index back to a name.
 *
 * Behaviour matched byte-for-byte against midi.js (see agent/tests):
 *  - channel is 1-based ((status & 0x0F) + 1)
 *  - 14-bit CC pairs (CC n<32 = MSB, n+32 = LSB) are combined and CONSUME the
 *    message: a plain-CC trigger on that number never sees a claimed pair
 *  - Note On with velocity 0 is a Note Off
 *  - Program Change / Pitch Bend / Aftertouch carry no discrete key beyond
 *    channel (PC/bend/AT); value scaling per type below
 *  - a macro fires on its FIRST matching trigger; every macro is considered
 *  - a knob macro whose matched trigger has use_value_as_param streams the
 *    value; otherwise the macro FIRES with param = use_value_as_param?value:1
 */
#ifndef TMOSC_MATCH_H
#define TMOSC_MATCH_H

#include "tmosc_midi.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TM_TRIG_CC = 0,
    TM_TRIG_CC14,
    TM_TRIG_NOTE_ON,
    TM_TRIG_NOTE_OFF,
    TM_TRIG_PROGRAM_CHANGE,
    TM_TRIG_PITCH_BEND,
    TM_TRIG_AFTERTOUCH
} tm_trig_type;

typedef struct {
    tm_trig_type type;
    int number;               /* CC / CC14-MSB / PC number; ignored otherwise */
    int note;                 /* note on/off; ignored otherwise */
    int channel;              /* 1..16 */
    int use_value_as_param;   /* 0/1 */
} tm_trigger;

typedef struct {
    const tm_trigger *triggers;
    int trigger_count;
    int is_knob;              /* macro has a knob step (streams instead of firing) */
} tm_macro;

typedef struct {
    const tm_macro *macros;
    int macro_count;
} tm_mapping;

typedef enum { TM_ACTION_KNOB, TM_ACTION_FIRE } tm_action_kind;

typedef struct {
    tm_action_kind kind;
    int macro_index;          /* index into mapping->macros */
    float value;              /* knob: 0..1 device value; fire: the param */
} tm_action;

/* Persistent 14-bit CC pairing state (mirrors midi.js `_cc14`). */
#ifndef TM_CC14_MAX
#define TM_CC14_MAX 32
#endif
typedef struct {
    int channel, msb, msb_val, lsb_val, used;
} tm_cc14_pair;
typedef struct {
    tm_cc14_pair pairs[TM_CC14_MAX];
} tm_match_state;

void tm_match_state_init(tm_match_state *s);

/* Match one decoded message against the mapping. Writes up to `cap` actions,
 * returns the number produced (0 for clock/sysex/unmatched). `state` may be
 * NULL only if the mapping has no CC14 triggers. */
int tm_match(tm_match_state *state, const tm_mapping *m,
             tm_midi_msg msg, tm_action *out, int cap);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_MATCH_H */

/* tmosc_match.c — see tmosc_match.h. Freestanding, no includes. */
#include "tmosc_match.h"

void tm_match_state_init(tm_match_state *s)
{
    int i;
    for (i = 0; i < TM_CC14_MAX; i++) {
        s->pairs[i].used = 0;
        s->pairs[i].channel = 0;
        s->pairs[i].msb = 0;
        s->pairs[i].msb_val = 0;
        s->pairs[i].lsb_val = 0;
    }
}

/* Does any macro carry a 14-bit-CC trigger listening on this (msb, channel)? */
static int cc14_msb_for(const tm_mapping *m, int cc, int channel)
{
    int msb = cc < 32 ? cc : cc - 32;
    int i, j;
    for (i = 0; i < m->macro_count; i++) {
        const tm_macro *mac = &m->macros[i];
        for (j = 0; j < mac->trigger_count; j++) {
            const tm_trigger *t = &mac->triggers[j];
            if (t->type == TM_TRIG_CC14 && t->number == msb && t->channel == channel)
                return msb;
        }
    }
    return -1;
}

static tm_cc14_pair *cc14_slot(tm_match_state *s, int channel, int msb)
{
    int i, free_i = -1;
    for (i = 0; i < TM_CC14_MAX; i++) {
        if (s->pairs[i].used && s->pairs[i].channel == channel && s->pairs[i].msb == msb)
            return &s->pairs[i];
        if (!s->pairs[i].used && free_i < 0) free_i = i;
    }
    if (free_i < 0) free_i = 0;   /* table full: reuse slot 0 (mirrors a rare case) */
    s->pairs[free_i].used = 1;
    s->pairs[free_i].channel = channel;
    s->pairs[free_i].msb = msb;
    s->pairs[free_i].msb_val = 0;
    s->pairs[free_i].lsb_val = 0;
    return &s->pairs[free_i];
}

/* Emit an action for every macro whose FIRST trigger of `type` on `channel`
 * with the matching key fires. key_kind: 0 = compare number, 1 = compare note,
 * 2 = no key (channel only, e.g. bend/aftertouch). */
static int emit_for(const tm_mapping *m, tm_trig_type type, int key, int key_kind,
                    int channel, float value, tm_action *out, int cap)
{
    int n = 0, i, j;
    for (i = 0; i < m->macro_count; i++) {
        const tm_macro *mac = &m->macros[i];
        for (j = 0; j < mac->trigger_count; j++) {
            const tm_trigger *t = &mac->triggers[j];
            int keyok = (key_kind == 0) ? (t->number == key)
                      : (key_kind == 1) ? (t->note == key)
                      : 1;
            if (t->type == type && t->channel == channel && keyok) {
                if (n < cap) {
                    if (mac->is_knob && t->use_value_as_param) {
                        out[n].kind = TM_ACTION_KNOB;
                        out[n].value = value;
                    } else {
                        out[n].kind = TM_ACTION_FIRE;
                        out[n].value = t->use_value_as_param ? value : 1.0f;
                    }
                    out[n].macro_index = i;
                }
                n++;
                break;   /* first matching trigger per macro wins */
            }
        }
    }
    return n < cap ? n : cap;
}

int tm_match(tm_match_state *state, const tm_mapping *m,
             tm_midi_msg msg, tm_action *out, int cap)
{
    int st = msg.status & 0xF0;
    int ch = (msg.status & 0x0F) + 1;
    int d1 = msg.data1, d2 = msg.data2;

    if (st == 0xB0) {
        int msb14 = cc14_msb_for(m, d1, ch);
        if (msb14 >= 0 && state) {
            tm_cc14_pair *p = cc14_slot(state, ch, msb14);
            if (d1 < 32) p->msb_val = d2; else p->lsb_val = d2;
            {
                int v14 = (p->msb_val << 7) | p->lsb_val;
                return emit_for(m, TM_TRIG_CC14, msb14, 0, ch,
                                (float)v14 / 16383.0f, out, cap);
            }
        }
        /* plain CC */
        return emit_for(m, TM_TRIG_CC, d1, 0, ch, (float)d2 / 127.0f, out, cap);
    }

    if (st == 0x90 || st == 0x80) {
        int is_on = (st == 0x90 && d2 > 0);
        return emit_for(m, is_on ? TM_TRIG_NOTE_ON : TM_TRIG_NOTE_OFF,
                        d1, 1, ch, (float)d2 / 127.0f, out, cap);
    }

    if (st == 0xC0)
        return emit_for(m, TM_TRIG_PROGRAM_CHANGE, d1, 0, ch, 1.0f, out, cap);

    if (st == 0xE0) {
        int v14 = d1 | (d2 << 7);
        return emit_for(m, TM_TRIG_PITCH_BEND, 0, 2, ch,
                        (float)v14 / 16383.0f, out, cap);
    }

    if (st == 0xD0)
        return emit_for(m, TM_TRIG_AFTERTOUCH, 0, 2, ch, (float)d1 / 127.0f, out, cap);

    return 0;   /* clock, sysex, system common: no actions */
}

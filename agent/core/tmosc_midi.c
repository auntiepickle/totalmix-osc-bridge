/* tmosc_midi.c — see tmosc_midi.h. Freestanding: no includes needed. */
#include "tmosc_midi.h"

void tm_midi_parser_init(tm_midi_parser *p)
{
    p->running_status = 0;
    p->expected = 0;
    p->data0 = 0;
    p->have_data0 = 0;
    p->in_sysex = 0;
}

/* Data bytes expected for a channel-voice status byte (high nibble):
 * 0xC0 (Program Change) and 0xD0 (Channel Aftertouch) take 1; the rest take 2. */
static int voice_data_len(int status)
{
    int hi = status & 0xF0;
    if (hi == 0xC0 || hi == 0xD0) return 1;
    return 2;
}

int tm_midi_parser_push(tm_midi_parser *p, unsigned char byte, tm_midi_msg *out)
{
    int b = (int)byte;

    /* System Real-Time (0xF8..0xFF): single byte, may interleave anywhere,
     * never affects running status. Emit immediately. */
    if (b >= 0xF8) {
        out->status = b;
        out->data1 = 0;
        out->data2 = 0;
        return 1;
    }

    if (p->in_sysex) {
        if (b == 0xF7) p->in_sysex = 0;   /* End of Exclusive */
        /* any other byte < 0xF8 is sysex payload (or an aborting status —
         * handled below once in_sysex clears) */
        if (b < 0x80) return 0;           /* payload byte: consume, no message */
        /* a status byte other than a realtime one aborts the sysex */
        p->in_sysex = 0;
        /* fall through to treat `b` as a new status byte */
    }

    if (b & 0x80) {
        /* status byte */
        if (b == 0xF0) { p->in_sysex = 1; p->running_status = 0; p->have_data0 = 0; return 0; }
        if (b == 0xF7) { p->running_status = 0; p->have_data0 = 0; return 0; }
        if (b >= 0xF1 && b <= 0xF6) {
            /* System Common: clears running status. F1/F3 carry one data byte,
             * F2 two, F4-F6 none. We don't route System Common to the matcher,
             * so track just enough to swallow their data bytes. */
            p->running_status = b;
            p->expected = (b == 0xF2) ? 2 : (b == 0xF1 || b == 0xF3) ? 1 : 0;
            p->have_data0 = 0;
            return 0;
        }
        /* channel voice status */
        p->running_status = b;
        p->expected = voice_data_len(b);
        p->have_data0 = 0;
        return 0;
    }

    /* data byte */
    if (p->running_status == 0) return 0;   /* orphan data, no status yet */

    /* Swallow System Common data bytes (running_status F1-F3) without emitting. */
    if (p->running_status >= 0xF1 && p->running_status <= 0xF6) {
        if (p->expected <= 1) { p->have_data0 = 0; return 0; }
        if (!p->have_data0) { p->have_data0 = 1; return 0; }
        p->have_data0 = 0;
        return 0;
    }

    if (p->expected == 1) {
        out->status = p->running_status;
        out->data1 = b;
        out->data2 = 0;
        return 1;   /* running status stays armed for the next message */
    }

    /* expected == 2 */
    if (!p->have_data0) {
        p->data0 = b;
        p->have_data0 = 1;
        return 0;
    }
    p->have_data0 = 0;
    out->status = p->running_status;
    out->data1 = p->data0;
    out->data2 = b;
    return 1;
}

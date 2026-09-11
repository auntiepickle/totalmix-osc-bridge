/* tmosc_bindings.c — see tmosc_bindings.h. Freestanding except string.h. */
#include "tmosc_bindings.h"
#include <string.h>

/* Parse a signed integer from [s,end); stores into *out. Returns ptr past it. */
static const char *parse_int(const char *s, const char *end, int *out)
{
    int sign = 1, val = 0, any = 0;
    if (s < end && *s == '-') { sign = -1; s++; }
    while (s < end && *s >= '0' && *s <= '9') { val = val * 10 + (*s - '0'); s++; any = 1; }
    *out = any ? sign * val : 0;
    return s;
}

static int field_eq(const char *s, const char *end, const char *lit)
{
    int n = (int)(end - s), i;
    for (i = 0; i < n; i++) if (s[i] != lit[i]) return 0;
    return lit[(size_t)n] == '\0';
}

static int trig_type_from(const char *s, const char *end, tm_trig_type *out)
{
    if (field_eq(s, end, "control_change"))    { *out = TM_TRIG_CC; return 1; }
    if (field_eq(s, end, "control_change_14")) { *out = TM_TRIG_CC14; return 1; }
    if (field_eq(s, end, "note_on"))           { *out = TM_TRIG_NOTE_ON; return 1; }
    if (field_eq(s, end, "note_off"))          { *out = TM_TRIG_NOTE_OFF; return 1; }
    if (field_eq(s, end, "program_change"))    { *out = TM_TRIG_PROGRAM_CHANGE; return 1; }
    if (field_eq(s, end, "pitch_bend"))        { *out = TM_TRIG_PITCH_BEND; return 1; }
    if (field_eq(s, end, "aftertouch"))        { *out = TM_TRIG_AFTERTOUCH; return 1; }
    return 0;
}

/* Find an existing macro by name, or create one. Returns index or -1 if full. */
static int macro_slot(tm_bindings *b, const char *name, int name_len, int is_knob)
{
    int i;
    if (name_len >= TM_NAME_LEN) name_len = TM_NAME_LEN - 1;
    for (i = 0; i < b->mapping.macro_count; i++) {
        if ((int)strlen(b->names[i]) == name_len &&
            strncmp(b->names[i], name, (size_t)name_len) == 0)
            return i;
    }
    if (b->mapping.macro_count >= TM_MAX_MACROS) return -1;
    i = b->mapping.macro_count++;
    memcpy(b->names[i], name, (size_t)name_len);
    b->names[i][name_len] = '\0';
    b->macros[i].triggers = &b->triggers[b->trig_used];   /* first trigger slot */
    b->macros[i].trigger_count = 0;
    b->macros[i].is_knob = is_knob;
    return i;
}

int tm_bindings_parse(tm_bindings *b, const char *tsv, int len)
{
    const char *p = tsv, *end = tsv + len;
    int overflow = 0;

    b->trig_used = 0;
    b->mapping.macros = b->macros;
    b->mapping.macro_count = 0;

    while (p < end) {
        const char *line = p, *le;
        const char *f[7];
        const char *fe[7];
        int nf = 0, i;

        /* line bounds */
        le = line;
        while (le < end && *le != '\n') le++;
        p = (le < end) ? le + 1 : end;
        /* strip a trailing CR */
        if (le > line && *(le - 1) == '\r') le--;
        if (le == line) continue;   /* blank */

        /* split into up to 7 tab fields */
        {
            const char *s = line;
            while (nf < 7) {
                const char *te = s;
                while (te < le && *te != '\t') te++;
                f[nf] = s; fe[nf] = te; nf++;
                if (te >= le) break;
                s = te + 1;
            }
        }
        if (nf < 7) continue;   /* malformed line: skip */

        {
            int is_knob = 0, number = -1, note = -1, channel = 1, uvap = 0, mi;
            tm_trig_type type;
            parse_int(f[1], fe[1], &is_knob);
            if (!trig_type_from(f[2], fe[2], &type)) continue;
            parse_int(f[3], fe[3], &number);
            parse_int(f[4], fe[4], &note);
            parse_int(f[5], fe[5], &channel);
            parse_int(f[6], fe[6], &uvap);

            mi = macro_slot(b, f[0], (int)(fe[0] - f[0]), is_knob);
            if (mi < 0) { overflow = 1; continue; }
            if (b->trig_used >= TM_MAX_TRIGGERS) { overflow = 1; continue; }
            /* enforce the contiguity invariant below instead of trusting the
             * feed: a macro line reappearing after another started would
             * otherwise extend the wrong block (review finding) */
            if (b->macros[mi].triggers + b->macros[mi].trigger_count != &b->triggers[b->trig_used]) {
                overflow = 1; continue;
            }

            /* triggers for a macro must be contiguous; we only ever append to
             * the macro created/extended most recently. Enforce by requiring
             * this macro's block to be at the tail. If an earlier macro line
             * reappears after another macro started, its triggers can't stay
             * contiguous - the bridge emits grouped-by-macro so this holds. */
            b->triggers[b->trig_used].type = type;
            b->triggers[b->trig_used].number = number;
            b->triggers[b->trig_used].note = note;
            b->triggers[b->trig_used].channel = channel;
            b->triggers[b->trig_used].use_value_as_param = uvap;
            b->macros[mi].trigger_count++;
            b->trig_used++;
            (void)i;
        }
    }
    return overflow ? -1 : 0;
}

const char *tm_bindings_name(const tm_bindings *b, int macro_index)
{
    if (macro_index < 0 || macro_index >= b->mapping.macro_count) return "";
    return b->names[macro_index];
}

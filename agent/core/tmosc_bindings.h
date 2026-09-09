/* tmosc_bindings.h — parse the bridge's /api/midi/bindings TSV into a mapping.
 *
 * Freestanding portable C: turns the tab-separated bindings feed into the
 * tm_mapping the matcher consumes, owning fixed-capacity storage (no malloc,
 * embedded-friendly). Line format (one per trigger):
 *   name \t is_knob \t type \t number \t note \t channel \t uvap
 * where type is one of: control_change control_change_14 note_on note_off
 * program_change pitch_bend aftertouch
 */
#ifndef TMOSC_BINDINGS_H
#define TMOSC_BINDINGS_H

#include "tmosc_match.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef TM_MAX_MACROS
#define TM_MAX_MACROS 128
#endif
#ifndef TM_MAX_TRIGGERS
#define TM_MAX_TRIGGERS 512
#endif
#ifndef TM_NAME_LEN
#define TM_NAME_LEN 64
#endif

typedef struct {
    tm_macro   macros[TM_MAX_MACROS];
    tm_trigger triggers[TM_MAX_TRIGGERS];
    char       names[TM_MAX_MACROS][TM_NAME_LEN];
    int        trig_used;
    tm_mapping mapping;   /* .macros = macros, .macro_count filled by parse */
} tm_bindings;

/* Parse `len` bytes of TSV into `b`. Returns 0 on success (even if 0 lines),
 * -1 if capacity was exceeded (partial result still usable). After success,
 * use &b->mapping with tm_match, and tm_bindings_name for index->name. */
int tm_bindings_parse(tm_bindings *b, const char *tsv, int len);

/* Macro name for an action's macro_index, or "" if out of range. */
const char *tm_bindings_name(const tm_bindings *b, int macro_index);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_BINDINGS_H */

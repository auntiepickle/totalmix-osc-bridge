/* test_core.c — dependency-free unit tests for the portable core.
 *
 * Cross-checks the C matcher against the exact behaviour of the browser's
 * midi.js (the shared spec). Build & run: exits non-zero on any failure.
 */
#include "tmosc_midi.h"
#include "tmosc_match.h"
#include "tmosc_clock.h"
#include "tmosc_proto.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

static int g_fail = 0;
#define CHECK(cond) do { if (!(cond)) { \
    printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); g_fail++; } } while (0)

static int approx(float a, float b) { return fabs((double)a - (double)b) < 1e-4; }

/* ---- fixtures: a small mapping ------------------------------------------- */
/* macro 0: knob "fader" on CC 82 ch1, use_value_as_param
   macro 1: fire "scene" on PC 5 ch1
   macro 2: knob "hicut" on CC14 MSB 10 ch1, use_value_as_param
   macro 3: fire "gate" on Note On 60 ch2
   macro 4: knob "pan" on Pitch Bend ch1, use_value_as_param */
static const tm_trigger t0[] = {{TM_TRIG_CC, 82, -1, 1, 1}};
static const tm_trigger t1[] = {{TM_TRIG_PROGRAM_CHANGE, 5, -1, 1, 0}};
static const tm_trigger t2[] = {{TM_TRIG_CC14, 10, -1, 1, 1}};
static const tm_trigger t3[] = {{TM_TRIG_NOTE_ON, -1, 60, 2, 0}};
static const tm_trigger t4[] = {{TM_TRIG_PITCH_BEND, -1, -1, 1, 1}};
static const tm_macro macros[] = {
    {t0, 1, 1}, {t1, 1, 0}, {t2, 1, 1}, {t3, 1, 0}, {t4, 1, 1},
};
static const tm_mapping MAP = {macros, 5};

static tm_midi_msg mk(int status, int d1, int d2)
{ tm_midi_msg m; m.status = status; m.data1 = d1; m.data2 = d2; return m; }

static void test_cc_knob(void)
{
    tm_match_state st; tm_action a[4]; int n;
    tm_match_state_init(&st);
    n = tm_match(&st, &MAP, mk(0xB0, 82, 127), a, 4);   /* CC82 ch1 = 127 */
    CHECK(n == 1);
    CHECK(a[0].kind == TM_ACTION_KNOB);
    CHECK(a[0].macro_index == 0);
    CHECK(approx(a[0].value, 1.0f));

    n = tm_match(&st, &MAP, mk(0xB0, 82, 64), a, 4);
    CHECK(n == 1 && approx(a[0].value, 64.0f / 127.0f));

    /* wrong channel -> no match */
    n = tm_match(&st, &MAP, mk(0xB1, 82, 100), a, 4);
    CHECK(n == 0);
}

static void test_pc_fire(void)
{
    tm_action a[4]; int n;
    n = tm_match(NULL, &MAP, mk(0xC0, 5, 0), a, 4);   /* PC5 ch1 */
    CHECK(n == 1);
    CHECK(a[0].kind == TM_ACTION_FIRE);
    CHECK(a[0].macro_index == 1);
    CHECK(approx(a[0].value, 1.0f));   /* PC has no value -> 1.0 */
}

static void test_cc14_pair_consumes(void)
{
    tm_match_state st; tm_action a[4]; int n;
    tm_match_state_init(&st);
    /* MSB (CC10) = 100, then LSB (CC42) = 20 -> v14 = (100<<7)|20 = 12820 */
    n = tm_match(&st, &MAP, mk(0xB0, 10, 100), a, 4);
    CHECK(n == 1 && a[0].macro_index == 2);
    CHECK(approx(a[0].value, (float)((100 << 7) | 0) / 16383.0f));
    n = tm_match(&st, &MAP, mk(0xB0, 42, 20), a, 4);
    CHECK(n == 1 && a[0].macro_index == 2);
    CHECK(approx(a[0].value, (float)((100 << 7) | 20) / 16383.0f));
    /* a plain-CC trigger on 10 would NOT have fired: the pair consumed it.
       (macro 2 is the only listener; verify no stray plain-CC action.) */
    CHECK(n == 1);
}

static void test_note_on_off(void)
{
    tm_action a[4]; int n;
    n = tm_match(NULL, &MAP, mk(0x91, 60, 100), a, 4);   /* note on ch2 */
    CHECK(n == 1 && a[0].macro_index == 3 && a[0].kind == TM_ACTION_FIRE);
    /* note on velocity 0 == note off: macro 3 listens for note_on, so 0 actions */
    n = tm_match(NULL, &MAP, mk(0x91, 60, 0), a, 4);
    CHECK(n == 0);
}

static void test_pitch_bend(void)
{
    tm_action a[4]; int n;
    /* bend ch1: data1=0, data2=64 -> v14 = 0 | (64<<7) = 8192 (center) */
    n = tm_match(NULL, &MAP, mk(0xE0, 0, 64), a, 4);
    CHECK(n == 1 && a[0].macro_index == 4 && a[0].kind == TM_ACTION_KNOB);
    CHECK(approx(a[0].value, 8192.0f / 16383.0f));
}

static void test_clock_ignored_by_matcher(void)
{
    tm_action a[4];
    CHECK(tm_match(NULL, &MAP, mk(0xF8, 0, 0), a, 4) == 0);
}

static void test_parser_stream(void)
{
    /* CC82=127 ch1, then running-status CC82=64, with a clock byte interleaved */
    unsigned char stream[] = {0xB0, 82, 127, 0xF8, 82, 64};
    tm_midi_parser p; tm_midi_msg out; int i, got = 0;
    int seen_clock = 0;
    tm_midi_parser_init(&p);
    for (i = 0; i < (int)sizeof(stream); i++) {
        if (tm_midi_parser_push(&p, stream[i], &out)) {
            if (out.status == 0xF8) { seen_clock = 1; continue; }
            got++;
            if (got == 1) { CHECK(out.status == 0xB0 && out.data1 == 82 && out.data2 == 127); }
            if (got == 2) { CHECK(out.status == 0xB0 && out.data1 == 82 && out.data2 == 64); }
        }
    }
    CHECK(got == 2);
    CHECK(seen_clock == 1);
}

static void test_parser_sysex_skip(void)
{
    /* F0 .. F7 sysex must be swallowed; a following note-on must parse. */
    unsigned char stream[] = {0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7, 0x90, 60, 100};
    tm_midi_parser p; tm_midi_msg out; int i, got = 0;
    tm_midi_parser_init(&p);
    for (i = 0; i < (int)sizeof(stream); i++) {
        if (tm_midi_parser_push(&p, stream[i], &out)) {
            got++;
            CHECK(out.status == 0x90 && out.data1 == 60 && out.data2 == 100);
        }
    }
    CHECK(got == 1);
}

static void test_clock_bpm(void)
{
    tm_clock c; int i, bpm = 0;
    tm_clock_init(&c);
    /* 120 BPM: quarter = 500ms, /24 = 20.833ms per clock */
    for (i = 0; i < 12; i++) bpm = tm_clock_tick(&c, i * (500.0 / 24.0));
    CHECK(bpm == 120);
    CHECK(c.bpm == 120);
}

static void test_proto(void)
{
    char buf[128];
    int n = tm_proto_knob_json(buf, sizeof(buf), "fader", 0.5f);
    CHECK(n > 0);
    CHECK(strcmp(buf, "{\"type\":\"knob\",\"name\":\"fader\",\"value\":0.5}") == 0);

    n = tm_proto_trigger_path(buf, sizeof(buf), "scene_1");
    CHECK(strcmp(buf, "/api/trigger/scene_1") == 0);

    n = tm_proto_trigger_body(buf, sizeof(buf), 1.0f, 128);
    CHECK(strcmp(buf, "{\"param\":1,\"clock_bpm\":128}") == 0);
    n = tm_proto_trigger_body(buf, sizeof(buf), 0.25f, 0);
    CHECK(strcmp(buf, "{\"param\":0.25}") == 0);
    (void)n;
}

int main(void)
{
    test_cc_knob();
    test_pc_fire();
    test_cc14_pair_consumes();
    test_note_on_off();
    test_pitch_bend();
    test_clock_ignored_by_matcher();
    test_parser_stream();
    test_parser_sysex_skip();
    test_clock_bpm();
    test_proto();

    if (g_fail) { printf("\n%d CHECK(s) FAILED\n", g_fail); return 1; }
    printf("all core tests passed\n");
    return 0;
}

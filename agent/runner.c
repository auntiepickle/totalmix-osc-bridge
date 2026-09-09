/* runner.c — see runner.h. Shared, platform-agnostic. */
#ifdef _WIN32
/* winsock2.h MUST precede windows.h (gethostname + the now_ms windows.h below). */
#  define WIN32_LEAN_AND_MEAN
#  include <winsock2.h>
#  include <windows.h>
#endif
#include "runner.h"
#include "tmosc_match.h"
#include "tmosc_clock.h"
#include "tmosc_bindings.h"
#include "tmosc_proto.h"
#include "net.h"

#include <stdio.h>
#include <string.h>

#define KNOB_MIN_MS   12
#define REFRESH_MS    5000
#define RECONNECT_MS  1000
#define HEARTBEAT_MS  2000   /* announce MIDI ownership so browsers yield Web MIDI */
#define ACTIVITY_MIN_MS 40   /* max ~25/s relayed to the browser (learn + live monitor) */

static volatile int g_stop = 0;
static int g_verbose = 0;

static tm_bindings    g_bind;
static tm_match_state g_mstate;
static float g_pending[TM_MAX_MACROS];
static int   g_dirty[TM_MAX_MACROS];

static char g_agent_id[96];    /* stable per-run identity: "<host>-tmosc-agent" */
static char g_agent_host[64];

static tm_midi_msg g_act;      /* latest raw message pending relay to the browser */
static int         g_act_have = 0;

void tm_runner_stop(void) { g_stop = 1; }

/* Monotonic-ish millisecond clock (portable). */
#ifdef _WIN32
  #include <windows.h>
  static double now_ms(void) { return (double)GetTickCount64(); }
  static void sleep_ms(int ms) { Sleep((DWORD)ms); }
#else
  #include <time.h>
  static double now_ms(void) {
      struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
      return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
  }
  static void sleep_ms(int ms) {
      struct timespec ts; ts.tv_sec = ms / 1000; ts.tv_nsec = (long)(ms % 1000) * 1000000L;
      nanosleep(&ts, NULL);
  }
#endif

static const char *trig_type_name(int t)
{
    switch (t) {
        case TM_TRIG_CC: return "cc";
        case TM_TRIG_CC14: return "cc14";
        case TM_TRIG_NOTE_ON: return "note_on";
        case TM_TRIG_NOTE_OFF: return "note_off";
        case TM_TRIG_PROGRAM_CHANGE: return "pc";
        case TM_TRIG_PITCH_BEND: return "bend";
        case TM_TRIG_AFTERTOUCH: return "at";
        default: return "?";
    }
}

static void print_bindings(void)
{
    int i, j;
    printf("loaded %d macro(s):\n", g_bind.mapping.macro_count);
    for (i = 0; i < g_bind.mapping.macro_count; i++) {
        const tm_macro *m = &g_bind.macros[i];
        printf("  %-24s %s\n", tm_bindings_name(&g_bind, i), m->is_knob ? "[knob]" : "[fire]");
        for (j = 0; j < m->trigger_count; j++) {
            const tm_trigger *t = &m->triggers[j];
            printf("      %-8s ch%-2d number=%d note=%d uvap=%d\n",
                   trig_type_name(t->type), t->channel, t->number, t->note,
                   t->use_value_as_param);
        }
    }
}

static int load_bindings(tm_net *net)
{
    static char buf[65536];
    int rlen = 0, status = 0;
    if (tm_net_request(net, "GET", "/api/midi/bindings", NULL,
                       buf, sizeof(buf), &rlen, &status) != 0) return -1;
    if (status != 200) return -1;
    tm_bindings_parse(&g_bind, buf, rlen);
    fprintf(stderr, "[agent] loaded %d macro(s) from bindings\n", g_bind.mapping.macro_count);
    return 0;
}

static int post_trigger(tm_net *net, const char *name, float param, int bpm)
{
    char path[256], body[64], resp[512];
    int rlen, status = 0, rc;
    if (tm_proto_trigger_path(path, sizeof(path), name) < 0) return 0;
    if (tm_proto_trigger_body(body, sizeof(body), param, bpm) < 0) return 0;
    rc = tm_net_request(net, "POST", path, body, resp, sizeof(resp), &rlen, &status);
    if (g_verbose) fprintf(stderr, "[agent] fire %-20s param=%.3f bpm=%d [%d]%s\n",
                           name, param, bpm, status, rc ? " NET-ERR" : "");
    return rc;
}

static int post_knob(tm_net *net, const char *name, float value)
{
    char path[256], body[48], resp[512];
    int rlen, status = 0, rc;
    if (tm_proto_knob_path(path, sizeof(path), name) < 0) return 0;
    if (tm_proto_knob_body(body, sizeof(body), value) < 0) return 0;
    rc = tm_net_request(net, "POST", path, body, resp, sizeof(resp), &rlen, &status);
    if (g_verbose) fprintf(stderr, "[agent] knob %-20s <- %.3f [%d]%s\n",
                           name, value, status, rc ? " NET-ERR" : "");
    return rc;
}

static int flush_knobs(tm_net *net)
{
    int i, err = 0;
    for (i = 0; i < g_bind.mapping.macro_count; i++) {
        if (!g_dirty[i]) continue;
        g_dirty[i] = 0;
        if (post_knob(net, tm_bindings_name(&g_bind, i), g_pending[i]) != 0) err = -1;
    }
    return err;
}

/* --- MIDI ownership heartbeat (coexistence with the browser) --------------- */
#ifndef _WIN32
  #include <unistd.h>     /* gethostname (Windows: via winsock2.h at top) */
#endif

static void init_agent_id(void)
{
    char host[64];
    if (g_agent_id[0]) return;
#ifdef _WIN32
    /* gethostname needs Winsock up; net.c only starts it on connect (later),
     * so do a balanced startup/cleanup here or the host falls back to "agent"
     * and every machine collides on the same owner id. */
    {
        WSADATA wsa;
        int wsa_ok = (WSAStartup(MAKEWORD(2, 2), &wsa) == 0);
        if (gethostname(host, (int)sizeof(host)) != 0 || !host[0])
            strcpy(host, "agent");
        if (wsa_ok) WSACleanup();
    }
#else
    if (gethostname(host, sizeof(host)) != 0 || !host[0])
        strcpy(host, "agent");
#endif
    host[sizeof(host) - 1] = '\0';
    snprintf(g_agent_host, sizeof(g_agent_host), "%s", host);
    snprintf(g_agent_id, sizeof(g_agent_id), "%s-tmosc-agent", host);
}

static int post_owner(tm_net *net, const char *path)
{
    char body[192], resp[256];
    int rlen, status = 0, rc;
    snprintf(body, sizeof(body), "{\"id\":\"%s\",\"host\":\"%s\"}", g_agent_id, g_agent_host);
    rc = tm_net_request(net, "POST", path, body, resp, sizeof(resp), &rlen, &status);
    if (g_verbose && rc == 0 && status != 200)
        fprintf(stderr, "[agent] owner %s [%d]\n", path, status);
    return rc;
}

/* Relay one raw MIDI message so a browser that yielded the port can still run
 * MIDI-learn and show live activity. Coalesced (latest wins per flush). */
static int post_activity(tm_net *net, const tm_midi_msg *m)
{
    char body[48], resp[128];
    int rlen, status = 0;
    snprintf(body, sizeof(body), "{\"m\":[%d,%d,%d]}",
             m->status & 0xFF, m->data1 & 0xFF, m->data2 & 0xFF);
    return tm_net_request(net, "POST", "/api/midi/activity", body, resp, sizeof(resp), &rlen, &status);
}

int tm_runner_dryrun(const char *host, int port)
{
    tm_net dn; dn.fd = -1;
    if (tm_net_connect(&dn, host, port) != 0) {
        fprintf(stderr, "cannot connect to bridge http://%s:%d\n", host, port);
        return 1;
    }
    if (load_bindings(&dn) != 0) { fprintf(stderr, "bindings fetch failed\n"); tm_net_close(&dn); return 1; }
    print_bindings();
    tm_net_close(&dn);
    return 0;
}

int tm_runner(const char *host, int port, const tm_midi_src *src, void *ctx, int verbose)
{
    tm_net net; net.fd = -1;
    tm_clock clock;
    double last_flush = 0, last_refresh = 0, last_reconnect = 0, last_heartbeat = 0, last_act_flush = 0;
    int connected = 0;

    g_verbose = verbose;
    g_stop = 0;
    init_agent_id();
    tm_clock_init(&clock);
    tm_match_state_init(&g_mstate);
    memset(g_dirty, 0, sizeof(g_dirty));

    while (!g_stop) {
        double t = now_ms();
        tm_midi_msg batch[256];
        int n, a, any_pending = 0, k, timeout;

        if (!connected) {
            if (t - last_reconnect < RECONNECT_MS) { sleep_ms(50); continue; }
            last_reconnect = t;
            if (tm_net_connect(&net, host, port) == 0 && load_bindings(&net) == 0) {
                connected = 1; last_refresh = t;
                fprintf(stderr, "[agent] connected\n");
            } else {
                tm_net_close(&net);
                fprintf(stderr, "[agent] connect failed, retrying\n");
                continue;
            }
        }

        for (k = 0; k < g_bind.mapping.macro_count; k++)
            if (g_dirty[k]) { any_pending = 1; break; }
        timeout = any_pending ? KNOB_MIN_MS : 200;
        src->wait(ctx, timeout);

        n = src->read(ctx, batch, 256);
        if (n < 0) { fprintf(stderr, "[agent] MIDI read error\n"); break; }
        for (a = 0; a < n; a++) {
            tm_midi_msg msg = batch[a];
            tm_action acts[16];
            int m, na;
            if ((msg.status & 0xFF) == 0xF8) { tm_clock_tick(&clock, now_ms()); continue; }
            if ((msg.status & 0xFF) < 0xF8) { g_act = msg; g_act_have = 1; }  /* relay latest raw msg */
            na = tm_match(&g_mstate, &g_bind.mapping, msg, acts, 16);
            for (m = 0; m < na; m++) {
                int idx = acts[m].macro_index;
                if (acts[m].kind == TM_ACTION_KNOB) {
                    if (idx >= 0 && idx < TM_MAX_MACROS) { g_pending[idx] = acts[m].value; g_dirty[idx] = 1; }
                } else {
                    if (post_trigger(&net, tm_bindings_name(&g_bind, idx), acts[m].value, clock.bpm) != 0)
                        connected = 0;
                }
            }
        }

        t = now_ms();
        if (t - last_flush >= KNOB_MIN_MS) {
            if (flush_knobs(&net) != 0) connected = 0;
            last_flush = t;
        }
        if (connected && t - last_refresh >= REFRESH_MS) {
            last_refresh = t;
            if (load_bindings(&net) != 0) connected = 0;
        }
        if (connected && t - last_heartbeat >= HEARTBEAT_MS) {
            last_heartbeat = t;
            if (post_owner(&net, "/api/midi/owner/heartbeat") != 0) connected = 0;
        }
        if (connected && g_act_have && t - last_act_flush >= ACTIVITY_MIN_MS) {
            last_act_flush = t; g_act_have = 0;
            if (post_activity(&net, &g_act) != 0) connected = 0;
        }
        if (!connected) tm_net_close(&net);
    }

    fprintf(stderr, "[agent] shutting down\n");
    /* best-effort release so the browser reclaims MIDI immediately (fresh
     * connection: the main one may be mid-failure at shutdown). */
    if (g_agent_id[0]) {
        tm_net rn; rn.fd = -1;
        if (tm_net_connect(&rn, host, port) == 0) post_owner(&rn, "/api/midi/owner/release");
        tm_net_close(&rn);
    }
    tm_net_close(&net);
    return 0;
}

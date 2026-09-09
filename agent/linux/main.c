/* main.c — TotalMix OSC agent, Linux daemon.
 *
 * Reads MIDI locally (ALSA), matches it against the bridge's trigger table
 * (fetched as TSV from /api/midi/bindings), and drives the bridge over HTTP:
 * knob CCs -> POST /api/knob/<name>, fire triggers -> POST /api/trigger/<name>.
 * No browser, no window. Runs as a systemd service on any Linux box the
 * controller plugs into (a server, a Pi, a PoE box).
 *
 * Config (env or argv):  TMOSC_BRIDGE_HOST TMOSC_BRIDGE_PORT TMOSC_MIDI
 *   argv:  tmosc-agent [host] [port] [midi-device]   |   tmosc-agent --list
 * TMOSC_MIDI accepts an ALSA id ("hw:1,0") OR a case-insensitive name
 * substring ("U6MIDI"); unset picks the first MIDI input. `--list` prints the
 * available inputs and exits.
 */
#define _POSIX_C_SOURCE 200809L
#include "tmosc_midi.h"
#include "tmosc_match.h"
#include "tmosc_clock.h"
#include "tmosc_bindings.h"
#include "tmosc_proto.h"
#include "net.h"
#include "midi_alsa.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <poll.h>

#define KNOB_MIN_MS       12      /* coalesced knob write cadence (~80 Hz) */
#define REFRESH_MS        5000    /* re-pull the bindings table */
#define RECONNECT_MS      1000

static volatile sig_atomic_t g_stop = 0;
static void on_signal(int s) { (void)s; g_stop = 1; }

static double now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

static tm_bindings   g_bind;
static tm_match_state g_mstate;

/* pending coalesced knob values, per macro index */
static float g_pending[TM_MAX_MACROS];
static int   g_dirty[TM_MAX_MACROS];

static int load_bindings(tm_net *net)
{
    static char buf[65536];
    int rlen = 0, status = 0;
    if (tm_net_request(net, "GET", "/api/midi/bindings", NULL,
                       buf, sizeof(buf), &rlen, &status) != 0) return -1;
    if (status != 200) return -1;
    tm_bindings_parse(&g_bind, buf, rlen);
    fprintf(stderr, "[agent] loaded %d macro(s) from bindings\n",
            g_bind.mapping.macro_count);
    return 0;
}

/* Fire a macro over HTTP. Returns 0 ok, -1 net error. */
static int post_trigger(tm_net *net, const char *name, float param, int bpm)
{
    char path[256], body[64], resp[512];
    int rlen, status;
    if (tm_proto_trigger_path(path, sizeof(path), name) < 0) return 0;
    if (tm_proto_trigger_body(body, sizeof(body), param, bpm) < 0) return 0;
    return tm_net_request(net, "POST", path, body, resp, sizeof(resp), &rlen, &status);
}

static int post_knob(tm_net *net, const char *name, float value)
{
    char path[256], body[48], resp[512];
    int rlen, status;
    if (tm_proto_knob_path(path, sizeof(path), name) < 0) return 0;
    if (tm_proto_knob_body(body, sizeof(body), value) < 0) return 0;
    return tm_net_request(net, "POST", path, body, resp, sizeof(resp), &rlen, &status);
}

/* Flush dirty knob values. Returns 0 ok, -1 if a net error occurred. */
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

int main(int argc, char **argv)
{
    const char *host = getenv("TMOSC_BRIDGE_HOST");
    const char *ports = getenv("TMOSC_BRIDGE_PORT");
    const char *mididev = getenv("TMOSC_MIDI");
    int port;
    tm_net net; net.fd = -1;
    tm_midi_alsa *midi = NULL;
    tm_midi_parser parser;
    tm_clock clock;
    double last_flush = 0, last_refresh = 0, last_reconnect = 0;
    int connected = 0;

    /* --list: print available MIDI inputs and exit */
    if (argc > 1 && strcmp(argv[1], "--list") == 0) {
        tm_midi_port pl[32];
        int c = tm_midi_alsa_list(pl, 32), i;
        if (c <= 0) { fprintf(stderr, "no MIDI input ports found\n"); return 1; }
        printf("MIDI input ports:\n");
        for (i = 0; i < c; i++) printf("  %-10s  %s\n", pl[i].port, pl[i].name);
        return 0;
    }

    if (argc > 1) host = argv[1];
    if (argc > 2) ports = argv[2];
    if (argc > 3) mididev = argv[3];
    if (!host) host = "127.0.0.1";
    if (!ports) ports = "8088";
    port = atoi(ports);

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    tm_midi_parser_init(&parser);
    tm_clock_init(&clock);
    tm_match_state_init(&g_mstate);
    memset(g_dirty, 0, sizeof(g_dirty));

    /* resolve the device query (hw:X,Y | name substring | first input) */
    {
        char resolved[64];
        if (tm_midi_alsa_resolve(mididev, resolved, sizeof(resolved)) != 0) {
            tm_midi_port pl[32];
            int c = tm_midi_alsa_list(pl, 32), i;
            fprintf(stderr, "[agent] no MIDI input matched '%s'. Available:\n",
                    mididev ? mididev : "(first)");
            for (i = 0; i < c; i++) fprintf(stderr, "    %-10s  %s\n", pl[i].port, pl[i].name);
            if (c == 0) fprintf(stderr, "    (none — is a controller plugged in?)\n");
            return 1;
        }
        midi = tm_midi_alsa_open(resolved);
        if (!midi) {
            fprintf(stderr, "[agent] cannot open MIDI '%s'\n", resolved);
            return 1;
        }
        fprintf(stderr, "[agent] MIDI %s (%s) open; bridge http://%s:%d\n",
                resolved, mididev ? mididev : "first input", host, port);
    }

    while (!g_stop) {
        double t = now_ms();

        /* (re)connect + load bindings */
        if (!connected) {
            if (t - last_reconnect < RECONNECT_MS) {
                struct timespec ts = {0, 50 * 1000000L};
                nanosleep(&ts, NULL);
                continue;
            }
            last_reconnect = t;
            if (tm_net_connect(&net, host, port) == 0 && load_bindings(&net) == 0) {
                connected = 1;
                last_refresh = t;
                fprintf(stderr, "[agent] connected\n");
            } else {
                tm_net_close(&net);
                fprintf(stderr, "[agent] connect failed, retrying\n");
                continue;
            }
        }

        /* wait for MIDI or the next flush/refresh deadline */
        {
            struct pollfd pfds[8];
            int nf = tm_midi_alsa_pollfds(midi, pfds, 8);
            int timeout = KNOB_MIN_MS;
            if (nf < 0) nf = 0;
            (void)poll(pfds, (nfds_t)nf, timeout);
        }

        /* drain MIDI */
        {
            unsigned char raw[256];
            int got = tm_midi_alsa_read(midi, raw, sizeof(raw));
            if (got < 0) { fprintf(stderr, "[agent] MIDI read error\n"); break; }
            for (int i = 0; i < got; i++) {
                tm_midi_msg msg;
                if (!tm_midi_parser_push(&parser, raw[i], &msg)) continue;
                if ((msg.status & 0xFF) == 0xF8) { tm_clock_tick(&clock, now_ms()); continue; }
                {
                    tm_action acts[16];
                    int n = tm_match(&g_mstate, &g_bind.mapping, msg, acts, 16), a;
                    for (a = 0; a < n; a++) {
                        int idx = acts[a].macro_index;
                        if (acts[a].kind == TM_ACTION_KNOB) {
                            if (idx >= 0 && idx < TM_MAX_MACROS) {
                                g_pending[idx] = acts[a].value;
                                g_dirty[idx] = 1;
                            }
                        } else {
                            if (post_trigger(&net, tm_bindings_name(&g_bind, idx),
                                             acts[a].value, clock.bpm) != 0) {
                                connected = 0;
                            }
                        }
                    }
                }
            }
        }

        /* coalesced knob flush */
        t = now_ms();
        if (t - last_flush >= KNOB_MIN_MS) {
            if (flush_knobs(&net) != 0) connected = 0;
            last_flush = t;
        }

        /* periodic bindings refresh */
        if (connected && t - last_refresh >= REFRESH_MS) {
            last_refresh = t;
            if (load_bindings(&net) != 0) connected = 0;
        }

        if (!connected) tm_net_close(&net);
    }

    fprintf(stderr, "[agent] shutting down\n");
    tm_net_close(&net);
    tm_midi_alsa_close(midi);
    return 0;
}

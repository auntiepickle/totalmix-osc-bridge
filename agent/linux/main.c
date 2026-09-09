/* main.c — TotalMix OSC agent, Linux entry point.
 *
 * Thin: it resolves the MIDI device, wraps ALSA rawmidi as a tm_midi_src, and
 * hands off to the shared runner (agent/runner.c). All matching/dispatch is
 * platform-agnostic there.
 *
 * Config (env or argv):  TMOSC_BRIDGE_HOST TMOSC_BRIDGE_PORT TMOSC_MIDI
 *   argv:  tmosc-agent [host] [port] [midi]  |  --list  |  --dry-run [host] [port]
 * TMOSC_MIDI: ALSA id ("hw:1,0"), a case-insensitive name substring, or unset
 * for the first input.  TMOSC_VERBOSE=1 logs each dispatched action.
 */
#define _POSIX_C_SOURCE 200809L
#include "tmosc_midi.h"
#include "runner.h"
#include "midi_alsa.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <poll.h>

static void on_signal(int s) { (void)s; tm_runner_stop(); }

/* ALSA MIDI source: read raw bytes, run them through the parser, emit msgs. */
typedef struct {
    tm_midi_alsa *m;
    tm_midi_parser parser;
} alsa_ctx;

static int alsa_read(void *vctx, tm_midi_msg *out, int max)
{
    alsa_ctx *c = (alsa_ctx *)vctx;
    unsigned char raw[256];
    int got, i, n = 0;
    got = tm_midi_alsa_read(c->m, raw, sizeof(raw));
    if (got < 0) return -1;
    for (i = 0; i < got && n < max; i++)
        if (tm_midi_parser_push(&c->parser, raw[i], &out[n])) n++;
    return n;
}

static void alsa_wait(void *vctx, int ms)
{
    alsa_ctx *c = (alsa_ctx *)vctx;
    struct pollfd pfds[8];
    int nf = tm_midi_alsa_pollfds(c->m, pfds, 8);
    if (nf < 0) nf = 0;
    (void)poll(pfds, (nfds_t)nf, ms);
}

int main(int argc, char **argv)
{
    const char *host = getenv("TMOSC_BRIDGE_HOST");
    const char *ports = getenv("TMOSC_BRIDGE_PORT");
    const char *mididev = getenv("TMOSC_MIDI");
    int verbose = getenv("TMOSC_VERBOSE") != NULL;
    int port;
    char resolved[64];
    alsa_ctx ctx;
    tm_midi_src src;

    if (argc > 1 && strcmp(argv[1], "--list") == 0) {
        tm_midi_port pl[32];
        int c = tm_midi_alsa_list(pl, 32), i;
        if (c <= 0) { fprintf(stderr, "no MIDI input ports found\n"); return 1; }
        printf("MIDI input ports:\n");
        for (i = 0; i < c; i++) printf("  %-10s  %s\n", pl[i].port, pl[i].name);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "--dry-run") == 0) {
        const char *h = argc > 2 ? argv[2] : (host ? host : "127.0.0.1");
        const char *ps = argc > 3 ? argv[3] : (ports ? ports : "8088");
        return tm_runner_dryrun(h, atoi(ps));
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

    if (tm_midi_alsa_resolve(mididev, resolved, sizeof(resolved)) != 0) {
        tm_midi_port pl[32];
        int c = tm_midi_alsa_list(pl, 32), i;
        fprintf(stderr, "[agent] no MIDI input matched '%s'. Available:\n", mididev ? mididev : "(first)");
        for (i = 0; i < c; i++) fprintf(stderr, "    %-10s  %s\n", pl[i].port, pl[i].name);
        if (c == 0) fprintf(stderr, "    (none — is a controller plugged in?)\n");
        return 1;
    }
    ctx.m = tm_midi_alsa_open(resolved);
    if (!ctx.m) { fprintf(stderr, "[agent] cannot open MIDI '%s'\n", resolved); return 1; }
    tm_midi_parser_init(&ctx.parser);
    fprintf(stderr, "[agent] MIDI %s (%s) open; bridge http://%s:%d\n",
            resolved, mididev ? mididev : "first input", host, port);

    src.read = alsa_read;
    src.wait = alsa_wait;
    tm_runner(host, port, &src, &ctx, verbose);

    tm_midi_alsa_close(ctx.m);
    return 0;
}

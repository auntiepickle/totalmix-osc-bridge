/* main_win.c — TotalMix OSC agent, Windows console entry point.
 *
 * Thin, like the Linux main: resolve the MIDI device, wrap WinMM as a
 * tm_midi_src, hand off to the shared runner. The tray GUI (later) wraps this
 * same runner call on a worker thread.
 *
 * Config (env or argv):  TMOSC_BRIDGE_HOST TMOSC_BRIDGE_PORT TMOSC_MIDI
 *   argv:  tmosc-agent [host] [port] [midi]  |  --list  |  --dry-run [host] [port]
 * TMOSC_MIDI: device index ("1"), a case-insensitive name substring
 * ("U6MIDI"), or unset for device 0.  TMOSC_VERBOSE=1 logs each action.
 */
#include "tmosc_midi.h"
#include "runner.h"
#include "midi_win.h"
#include "net.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

static BOOL WINAPI ctrl_handler(DWORD type)
{
    (void)type;
    tm_runner_stop();
    return TRUE;
}

static int win_read(void *ctx, tm_midi_msg *out, int max)
{
    return tm_midi_win_read((tm_midi_win *)ctx, out, max);
}
static void win_wait(void *ctx, int ms)
{
    tm_midi_win_wait((tm_midi_win *)ctx, ms);
}

int main(int argc, char **argv)
{
    const char *host = getenv("TMOSC_BRIDGE_HOST");
    const char *ports = getenv("TMOSC_BRIDGE_PORT");
    const char *mididev = getenv("TMOSC_MIDI");
    int verbose = getenv("TMOSC_VERBOSE") != NULL;
    int port;
    char resolved[64];
    tm_midi_win *m;
    tm_midi_src src;

    if (argc > 1 && strcmp(argv[1], "--list") == 0) {
        tm_midi_port pl[64];
        int c = tm_midi_win_list(pl, 64), i;
        if (c <= 0) { fprintf(stderr, "no MIDI input ports found\n"); return 1; }
        printf("MIDI input ports:\n");
        for (i = 0; i < c; i++) printf("  %-4s  %s\n", pl[i].port, pl[i].name);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "--dry-run") == 0) {
        const char *h = argc > 2 ? argv[2] : (host ? host : "127.0.0.1");
        const char *ps = argc > 3 ? argv[3] : (ports ? ports : "8088");
        return tm_runner_dryrun(h, atoi(ps));
    }
    if (argc > 1 && strcmp(argv[1], "--discover") == 0) {
        /* auto-find the bridge on the LAN; prints its IP (used by the installer). */
        int dp = argc > 2 ? atoi(argv[2]) : (ports ? atoi(ports) : 8088);
        char found[64];
        if (tm_net_discover(dp, found, (int)sizeof(found)) == 0) { printf("%s\n", found); return 0; }
        fprintf(stderr, "no TotalMix OSC bridge found on the LAN\n");
        return 1;
    }

    if (argc > 1) host = argv[1];
    if (argc > 2) ports = argv[2];
    if (argc > 3) mididev = argv[3];
    if (!host) host = "127.0.0.1";
    if (!ports) ports = "8088";
    port = atoi(ports);

    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    if (tm_midi_win_resolve(mididev, resolved, sizeof(resolved)) != 0) {
        tm_midi_port pl[64];
        int c = tm_midi_win_list(pl, 64), i;
        fprintf(stderr, "[agent] no MIDI input matched '%s'. Available:\n", mididev ? mididev : "(first)");
        for (i = 0; i < c; i++) fprintf(stderr, "    %-4s  %s\n", pl[i].port, pl[i].name);
        if (c == 0) fprintf(stderr, "    (none - is a controller plugged in?)\n");
        return 1;
    }
    m = tm_midi_win_open(resolved);
    if (!m) { fprintf(stderr, "[agent] cannot open MIDI device %s\n", resolved); return 1; }
    fprintf(stderr, "[agent] MIDI device %s (%s) open; bridge http://%s:%d\n",
            resolved, mididev ? mididev : "first input", host, port);

    src.read = win_read;
    src.wait = win_wait;
    tm_runner(host, port, &src, m, verbose);

    tm_midi_win_close(m);
    return 0;
}

/* tray_win.c — TotalMix OSC agent, Windows system-tray front end.
 *
 * A GUI-subsystem app (no console window): it sits in the notification area,
 * runs the shared runner on a worker thread (headless MIDI -> bridge, no
 * browser), and its menu launches the full web UI on demand.
 *
 * Config: %APPDATA%\tmosc-agent\config.txt (key=value: host, port, midi),
 * falling back to the TMOSC_* env vars, then defaults (127.0.0.1:8088, first
 * MIDI input). This is the same runner as the console build.
 */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "runner.h"
#include "midi_win.h"

#define WM_TRAY   (WM_APP + 1)
#define ID_OPEN   1001
#define ID_QUIT   1002

static NOTIFYICONDATAA g_nid;
static HWND   g_hwnd;
static HANDLE g_thread;
static char   g_host[128] = "127.0.0.1";
static int    g_port = 8088;
static char   g_midi[80] = "";
static char   g_url[192];

static void set_str(char *dst, size_t cap, const char *src)
{
    if (!src) return;
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

static void load_config(void)
{
    const char *h = getenv("TMOSC_BRIDGE_HOST");
    const char *p = getenv("TMOSC_BRIDGE_PORT");
    const char *m = getenv("TMOSC_MIDI");
    const char *ad;
    if (h) set_str(g_host, sizeof(g_host), h);
    if (p) g_port = atoi(p);
    if (m) set_str(g_midi, sizeof(g_midi), m);

    ad = getenv("APPDATA");
    if (ad) {
        char path[MAX_PATH];
        FILE *f;
        snprintf(path, sizeof(path), "%s\\tmosc-agent\\config.txt", ad);
        f = fopen(path, "r");
        if (f) {
            char line[256];
            while (fgets(line, sizeof(line), f)) {
                char *eq = strchr(line, '=');
                char *nl, *k, *v;
                if (!eq) continue;
                *eq = '\0'; k = line; v = eq + 1;
                nl = strpbrk(v, "\r\n"); if (nl) *nl = '\0';
                if      (!strcmp(k, "host")) set_str(g_host, sizeof(g_host), v);
                else if (!strcmp(k, "port")) g_port = atoi(v);
                else if (!strcmp(k, "midi")) set_str(g_midi, sizeof(g_midi), v);
            }
            fclose(f);
        }
    }
    snprintf(g_url, sizeof(g_url), "http://%s:%d", g_host, g_port);
}

static int  win_read(void *ctx, tm_midi_msg *out, int max) { return tm_midi_win_read((tm_midi_win *)ctx, out, max); }
static void win_wait(void *ctx, int ms) { tm_midi_win_wait((tm_midi_win *)ctx, ms); }

static DWORD WINAPI worker(LPVOID arg)
{
    char resolved[64];
    const char *q = g_midi[0] ? g_midi : NULL;
    tm_midi_win *m;
    tm_midi_src src;
    (void)arg;
    if (tm_midi_win_resolve(q, resolved, sizeof(resolved)) != 0) return 1;
    m = tm_midi_win_open(resolved);
    if (!m) return 1;
    src.read = win_read;
    src.wait = win_wait;
    tm_runner(g_host, g_port, &src, m, 0);
    tm_midi_win_close(m);
    return 0;
}

static void show_menu(HWND hwnd)
{
    POINT pt;
    HMENU menu = CreatePopupMenu();
    char item[220];
    GetCursorPos(&pt);
    snprintf(item, sizeof(item), "Open Web UI  (%s)", g_url);
    AppendMenuA(menu, MF_STRING, ID_OPEN, item);
    AppendMenuA(menu, MF_SEPARATOR, 0, NULL);
    AppendMenuA(menu, MF_STRING, ID_QUIT, "Quit");
    SetForegroundWindow(hwnd);   /* so the menu dismisses on focus loss */
    TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, NULL);
    DestroyMenu(menu);
}

static LRESULT CALLBACK wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
        case WM_TRAY:
            if (lp == WM_RBUTTONUP || lp == WM_CONTEXTMENU) show_menu(hwnd);
            else if (lp == WM_LBUTTONDBLCLK) ShellExecuteA(NULL, "open", g_url, NULL, NULL, SW_SHOWNORMAL);
            return 0;
        case WM_COMMAND:
            if (LOWORD(wp) == ID_OPEN) ShellExecuteA(NULL, "open", g_url, NULL, NULL, SW_SHOWNORMAL);
            else if (LOWORD(wp) == ID_QUIT) { tm_runner_stop(); DestroyWindow(hwnd); }
            return 0;
        case WM_DESTROY:
            Shell_NotifyIconA(NIM_DELETE, &g_nid);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR cmdline, int show)
{
    WNDCLASSA wc;
    MSG msg;
    (void)hPrev; (void)cmdline; (void)show;

    load_config();

    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = wndproc;
    wc.hInstance = hInst;
    wc.lpszClassName = "TmoscAgentTray";
    RegisterClassA(&wc);
    g_hwnd = CreateWindowA("TmoscAgentTray", "TotalMix OSC Agent", 0,
                           0, 0, 0, 0, HWND_MESSAGE, NULL, hInst, NULL);

    memset(&g_nid, 0, sizeof(g_nid));
    g_nid.cbSize = sizeof(g_nid);
    g_nid.hWnd = g_hwnd;
    g_nid.uID = 1;
    g_nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAY;
    g_nid.hIcon = LoadIcon(NULL, IDI_APPLICATION);
    snprintf(g_nid.szTip, sizeof(g_nid.szTip), "TotalMix OSC Agent - %s", g_url);
    Shell_NotifyIconA(NIM_ADD, &g_nid);

    g_thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);

    while (GetMessage(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    tm_runner_stop();
    if (g_thread) { WaitForSingleObject(g_thread, 2000); CloseHandle(g_thread); }
    return 0;
}

/* midi_win.c — see midi_win.h. Links against winmm. */
#include "midi_win.h"
#include "tmosc_suspend.h"
#include <windows.h>
#include <mmsystem.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define RING 1024
#define SUSPEND_MIN_MS 1000.0    /* shorter gaps are clock jitter, not a sleep */

struct tm_midi_win {
    HMIDIIN h;
    HANDLE  evt;                 /* set when a message is enqueued */
    CRITICAL_SECTION lock;
    tm_midi_msg ring[RING];
    volatile int head, tail;     /* head=write, tail=read */
    UINT dev;                    /* device index we opened */
    char name[80];               /* its name then, to spot a re-enumeration */
    tm_suspend susp;             /* resume detector (see tm_midi_win_health) */
};

int tm_midi_win_list(tm_midi_port *out, int max)
{
    UINT i, num = midiInGetNumDevs();
    int n = 0;
    for (i = 0; i < num && n < (UINT)max; i++) {
        MIDIINCAPSA caps;
        if (midiInGetDevCapsA(i, &caps, sizeof(caps)) != MMSYSERR_NOERROR) continue;
        snprintf(out[n].port, sizeof(out[n].port), "%u", i);
        snprintf(out[n].name, sizeof(out[n].name), "%s", caps.szPname);
        n++;
    }
    return n;
}

static int ci_contains(const char *hay, const char *needle)
{
    size_t nl = strlen(needle), i;
    if (nl == 0) return 1;
    for (; *hay; hay++) {
        for (i = 0; i < nl; i++) {
            if (tolower((unsigned char)hay[i]) != tolower((unsigned char)needle[i])) break;
            if (hay[i] == '\0') return 0;
        }
        if (i == nl) return 1;
    }
    return 0;
}

int tm_midi_win_resolve(const char *query, char *out, int outlen)
{
    tm_midi_port ports[64];
    int cnt, i, alldig = 1;
    const char *p;
    if (query && *query) {
        for (p = query; *p; p++) if (!isdigit((unsigned char)*p)) { alldig = 0; break; }
        if (alldig) { snprintf(out, (size_t)outlen, "%s", query); return 0; }
    }
    cnt = tm_midi_win_list(ports, 64);
    if (cnt <= 0) return -1;
    if (query && *query) {
        for (i = 0; i < cnt; i++)
            if (ci_contains(ports[i].name, query)) { snprintf(out, (size_t)outlen, "%s", ports[i].port); return 0; }
        return -1;
    }
    snprintf(out, (size_t)outlen, "%s", ports[0].port);
    return 0;
}

static void CALLBACK midi_cb(HMIDIIN h, UINT msg, DWORD_PTR inst, DWORD_PTR p1, DWORD_PTR p2)
{
    struct tm_midi_win *m = (struct tm_midi_win *)inst;
    (void)h; (void)p2;
    if (msg != MIM_DATA) return;
    {
        DWORD dw = (DWORD)p1;
        int next;
        EnterCriticalSection(&m->lock);
        next = (m->head + 1) % RING;
        if (next != m->tail) {                 /* drop if full */
            m->ring[m->head].status = (int)(dw & 0xFF);
            m->ring[m->head].data1  = (int)((dw >> 8) & 0xFF);
            m->ring[m->head].data2  = (int)((dw >> 16) & 0xFF);
            m->head = next;
        }
        LeaveCriticalSection(&m->lock);
        SetEvent(m->evt);
    }
}

tm_midi_win *tm_midi_win_open(const char *index_str)
{
    struct tm_midi_win *m = (struct tm_midi_win *)calloc(1, sizeof(*m));
    UINT dev;
    if (!m) return NULL;
    dev = (UINT)atoi(index_str ? index_str : "0");
    m->evt = CreateEvent(NULL, FALSE, FALSE, NULL);
    InitializeCriticalSection(&m->lock);
    if (midiInOpen(&m->h, dev, (DWORD_PTR)midi_cb, (DWORD_PTR)m, CALLBACK_FUNCTION) != MMSYSERR_NOERROR) {
        DeleteCriticalSection(&m->lock);
        if (m->evt) CloseHandle(m->evt);
        free(m);
        return NULL;
    }
    m->dev = dev;
    {   /* remember which device this index was, for tm_midi_win_health */
        MIDIINCAPSA caps;
        if (midiInGetDevCapsA(dev, &caps, sizeof(caps)) == MMSYSERR_NOERROR)
            snprintf(m->name, sizeof(m->name), "%s", caps.szPname);
    }
    tm_suspend_init(&m->susp);
    midiInStart(m->h);
    return m;
}

/* Milliseconds of interrupt time excluding any suspend, or -1 where the OS
 * cannot tell us. Resolved at runtime: QueryUnbiasedInterruptTime needs
 * _WIN32_WINNT >= 0x0601 to be declared, and looking it up keeps the build
 * working across MinGW and MSVC whatever SDK version they target. */
typedef BOOL (WINAPI *tm_unbiased_fn)(PULONGLONG);

static double unbiased_ms(void)
{
    static tm_unbiased_fn fn = NULL;
    static int looked_up = 0;
    ULONGLONG t100ns;
    if (!looked_up) {
        HMODULE k = GetModuleHandleA("kernel32.dll");
        if (k) fn = (tm_unbiased_fn)(void *)GetProcAddress(k, "QueryUnbiasedInterruptTime");
        looked_up = 1;
    }
    if (!fn || !fn(&t100ns)) return -1.0;
    return (double)(t100ns / 10000ULL);
}

int tm_midi_win_health(tm_midi_win *m)
{
    MIDIINCAPSA caps;
    double ub;
    if (!m) return -1;

    /* 1. Resume. The handle stays valid-looking across a suspend but the
     *    re-enumerated device never feeds it again, so always reopen. */
    ub = unbiased_ms();
    if (ub >= 0.0
        && tm_suspend_check(&m->susp, (double)GetTickCount64(), ub, SUSPEND_MIN_MS) > 0.0)
        return -1;

    /* 2. Re-enumeration. Our index now names a different device, or none —
     *    reopening re-resolves the configured name to wherever it landed. */
    if (m->dev >= midiInGetNumDevs()) return -1;
    if (midiInGetDevCapsA(m->dev, &caps, sizeof(caps)) != MMSYSERR_NOERROR) return -1;
    if (m->name[0] && strcmp(caps.szPname, m->name) != 0) return -1;
    return 0;
}

void tm_midi_win_close(tm_midi_win *m)
{
    if (!m) return;
    if (m->h) { midiInStop(m->h); midiInReset(m->h); midiInClose(m->h); }
    DeleteCriticalSection(&m->lock);
    if (m->evt) CloseHandle(m->evt);
    free(m);
}

int tm_midi_win_read(tm_midi_win *m, tm_midi_msg *out, int max)
{
    int n = 0;
    EnterCriticalSection(&m->lock);
    while (n < max && m->tail != m->head) {
        out[n++] = m->ring[m->tail];
        m->tail = (m->tail + 1) % RING;
    }
    LeaveCriticalSection(&m->lock);
    return n;
}

void tm_midi_win_wait(tm_midi_win *m, int ms)
{
    WaitForSingleObject(m->evt, (DWORD)ms);
}

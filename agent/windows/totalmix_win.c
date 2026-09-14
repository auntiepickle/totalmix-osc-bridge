/* totalmix_win.c — see totalmix_win.h. */
#include <windows.h>
#include <string.h>
#include "totalmix_win.h"

struct tm_title_find {
    char *buf;
    int cap;
    int len;
};

/* Only the main window carries the "RME TotalMix FX" prefix; the Quick
 * Workspace Select popup is titled just "TotalMix FX" and the tray agent's
 * own hidden window "TotalMix OSC Agent". */
static BOOL CALLBACK tm_title_enum(HWND hwnd, LPARAM lp)
{
    struct tm_title_find *f = (struct tm_title_find *)lp;
    wchar_t w[512];
    int n = GetWindowTextW(hwnd, w, (int)(sizeof(w) / sizeof(w[0])));
    if (n <= 0) return TRUE;
    if (wcsncmp(w, L"RME TotalMix FX", 15) != 0) return TRUE;
    n = WideCharToMultiByte(CP_UTF8, 0, w, -1, f->buf, f->cap, NULL, NULL);
    if (n <= 0) {                       /* did not fit: report nothing rather than a truncated title */
        f->buf[0] = '\0';
        f->len = 0;
        return TRUE;
    }
    f->len = n - 1;                     /* WideCharToMultiByte counts the NUL */
    return FALSE;                       /* first match wins */
}

int tm_totalmix_title(char *buf, int cap)
{
    struct tm_title_find f;
    if (!buf || cap <= 0) return 0;
    buf[0] = '\0';
    f.buf = buf;
    f.cap = cap;
    f.len = 0;
    EnumWindows(tm_title_enum, (LPARAM)&f);
    return f.len;
}

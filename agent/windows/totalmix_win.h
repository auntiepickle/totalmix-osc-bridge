/* totalmix_win.h — the TotalMix FX main-window title (#30).
 *
 * TotalMix shows the loaded workspace in its title bar ("RME TotalMix FX:
 * Fireface UFX II (1) - 48.0k - Work", or the .tmws path for a file-loaded
 * workspace) and nowhere else that is observable live - no OSC feed, no
 * file. The agent sends the raw title with its heartbeat; the bridge parses
 * it, so a format change on RME's side never needs a tray rebuild.
 */
#ifndef TM_TOTALMIX_WIN_H
#define TM_TOTALMIX_WIN_H

/* Copy the title of the top-level "RME TotalMix FX" window into buf as
 * UTF-8 (NUL-terminated, cap bytes). Returns the length, 0 when no such
 * window exists (TotalMix not running on this machine). Hidden windows
 * count: TotalMix minimised to the tray still has its main window. */
int tm_totalmix_title(char *buf, int cap);

#endif /* TM_TOTALMIX_WIN_H */

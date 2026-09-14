/* net.h — minimal HTTP/1.1 keep-alive client. Cross-platform: POSIX sockets on
 * Linux/macOS, Winsock on Windows. Just enough for the agent: one request at a
 * time, Content-Length responses, no TLS (the bridge is plain HTTP on the LAN). */
#ifndef TM_NET_H
#define TM_NET_H

#include <stdint.h>

typedef struct {
    intptr_t fd;      /* socket handle (SOCKET on Windows fits in intptr_t) */
    char host[128];
    int port;
} tm_net;

/* Optional shared API token (the bridge's API_TOKEN, docs/security.md): once
 * set, every request carries "X-Api-Token: <token>". Process-wide, because
 * the runner opens several short-lived connections (dry-run, announce, the
 * shutdown release) that must all authenticate the same way. NULL or ""
 * clears it. Longer values are truncated to TM_NET_TOKEN_MAX-1 chars. */
#define TM_NET_TOKEN_MAX 128
void tm_net_set_token(const char *token);
int  tm_net_has_token(void);

/* Connect (or reconnect). Returns 0 ok, -1 on failure. */
int tm_net_connect(tm_net *n, const char *host, int port);
void tm_net_close(tm_net *n);

/* One HTTP request. method="GET"/"POST"; body may be NULL. The response body
 * (up to resp_cap-1 bytes, NUL-terminated) goes to resp; *resp_len and *status
 * are set. Returns 0 ok, -1 on I/O error (caller should reconnect + retry). */
int tm_net_request(tm_net *n, const char *method, const char *path,
                   const char *body, char *resp, int resp_cap,
                   int *resp_len, int *status);

/* Auto-discover a bridge on the LAN: UDP-broadcast a ping to <port> (the
 * limited broadcast plus every interface's directed broadcast, so a bridge on
 * a second adapter/segment is reached too) and take the first responder's IP
 * into host_out. Returns 0 if a bridge was found, -1 otherwise (caller falls
 * back to a configured/entered host). */
int tm_net_discover(int port, char *host_out, int host_cap);

#endif /* TM_NET_H */

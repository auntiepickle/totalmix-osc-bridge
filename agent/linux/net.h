/* net.h — minimal HTTP/1.1 keep-alive client over POSIX sockets (Linux/macOS).
 * Just enough for the agent: one request at a time, Content-Length responses.
 * No TLS (the bridge is plain HTTP on the LAN); portable to Winsock later. */
#ifndef TM_NET_H
#define TM_NET_H

typedef struct {
    int fd;
    char host[128];
    int port;
} tm_net;

/* Connect (or reconnect). Returns 0 ok, -1 on failure. */
int tm_net_connect(tm_net *n, const char *host, int port);
void tm_net_close(tm_net *n);

/* One HTTP request. method="GET"/"POST"; body may be NULL. The response body
 * (up to resp_cap-1 bytes, NUL-terminated) goes to resp; *resp_len and *status
 * are set. Returns 0 ok, -1 on I/O error (caller should reconnect + retry). */
int tm_net_request(tm_net *n, const char *method, const char *path,
                   const char *body, char *resp, int resp_cap,
                   int *resp_len, int *status);

#endif /* TM_NET_H */

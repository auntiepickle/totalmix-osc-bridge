/* net.c — see net.h. */
#define _POSIX_C_SOURCE 200112L
#include "net.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <netdb.h>

void tm_net_close(tm_net *n)
{
    if (n->fd >= 0) { close(n->fd); n->fd = -1; }
}

int tm_net_connect(tm_net *n, const char *host, int port)
{
    struct addrinfo hints, *res = NULL, *ai;
    char portstr[16];
    int fd = -1;

    tm_net_close(n);
    if (host != n->host) {
        strncpy(n->host, host, sizeof(n->host) - 1);
        n->host[sizeof(n->host) - 1] = '\0';
    }
    n->port = port;
    snprintf(portstr, sizeof(portstr), "%d", port);

    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(n->host, portstr, &hints, &res) != 0) return -1;

    for (ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return -1;

    { int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one)); }
    n->fd = fd;
    return 0;
}

static int write_all(int fd, const char *buf, int len)
{
    int off = 0;
    while (off < len) {
        ssize_t k = send(fd, buf + off, (size_t)(len - off), 0);
        if (k < 0) { if (errno == EINTR) continue; return -1; }
        off += (int)k;
    }
    return 0;
}

/* Read one byte; returns 1 ok, 0 on EOF, -1 error. */
static int read_byte(int fd, char *c)
{
    for (;;) {
        ssize_t k = recv(fd, c, 1, 0);
        if (k == 1) return 1;
        if (k == 0) return 0;
        if (errno == EINTR) continue;
        return -1;
    }
}

int tm_net_request(tm_net *n, const char *method, const char *path,
                   const char *body, char *resp, int resp_cap,
                   int *resp_len, int *status)
{
    char req[1024];
    int blen = body ? (int)strlen(body) : 0;
    int rn;
    char line[512];
    int content_length = -1;
    int i;

    if (n->fd < 0) return -1;

    rn = snprintf(req, sizeof(req),
        "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: keep-alive\r\n"
        "%s%sContent-Length: %d\r\n\r\n",
        method, path, n->host, n->port,
        body ? "Content-Type: application/json\r\n" : "",
        "", blen);
    if (rn < 0 || rn >= (int)sizeof(req)) return -1;
    if (write_all(n->fd, req, rn) != 0) return -1;
    if (blen && write_all(n->fd, body, blen) != 0) return -1;

    /* status line */
    { int li = 0; char c;
      for (;;) {
          int r = read_byte(n->fd, &c);
          if (r <= 0) return -1;
          if (c == '\n') break;
          if (li < (int)sizeof(line) - 1) line[li++] = c;
      }
      line[li] = '\0';
    }
    /* "HTTP/1.1 200 OK" */
    { int code = 0; const char *sp = strchr(line, ' ');
      if (!sp) return -1;
      code = atoi(sp + 1);
      if (status) *status = code;
    }

    /* headers until blank line */
    for (;;) {
        int li = 0; char c;
        for (;;) {
            int r = read_byte(n->fd, &c);
            if (r <= 0) return -1;
            if (c == '\n') break;
            if (c != '\r' && li < (int)sizeof(line) - 1) line[li++] = c;
        }
        line[li] = '\0';
        if (li == 0) break;   /* end of headers */
        /* case-insensitive Content-Length */
        if (li > 15) {
            char h[16]; int k;
            for (k = 0; k < 15; k++) h[k] = (char)((line[k] >= 'A' && line[k] <= 'Z') ? line[k] + 32 : line[k]);
            h[15] = '\0';
            if (strcmp(h, "content-length:") == 0) content_length = atoi(line + 15);
        }
    }

    if (content_length < 0) return -1;   /* our endpoints always set it */

    /* body: exactly content_length bytes */
    { int got = 0;
      while (got < content_length) {
          char c; int r = read_byte(n->fd, &c);
          if (r <= 0) return -1;
          if (got < resp_cap - 1) resp[got] = c;
          got++;
      }
      i = got < resp_cap - 1 ? got : resp_cap - 1;
      resp[i] = '\0';
      if (resp_len) *resp_len = i;
    }
    return 0;
}

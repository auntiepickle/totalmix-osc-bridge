/* net.c — see net.h. Cross-platform (POSIX sockets / Winsock). */
#include "net.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifdef _WIN32
  #include <winsock2.h>
  #include <ws2tcpip.h>
  typedef SOCKET sock_t;
  #define SOCK_BAD  INVALID_SOCKET
  #define sock_close closesocket
  #define SOCKOPT_CAST (const char *)
  static int net_started = 0;
  static void net_startup(void) {
      if (!net_started) { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); net_started = 1; }
  }
#else
  #include <unistd.h>
  #include <errno.h>
  #include <sys/socket.h>
  #include <netinet/in.h>
  #include <netinet/tcp.h>
  #include <arpa/inet.h>
  #include <netdb.h>
  typedef int sock_t;
  #define SOCK_BAD  (-1)
  #define sock_close close
  #define SOCKOPT_CAST (const void *)
  static void net_startup(void) {}
#endif

static sock_t as_sock(intptr_t fd) { return (sock_t)fd; }

void tm_net_close(tm_net *n)
{
    if (n->fd != (intptr_t)SOCK_BAD) { sock_close(as_sock(n->fd)); n->fd = (intptr_t)SOCK_BAD; }
}

int tm_net_connect(tm_net *n, const char *host, int port)
{
    struct addrinfo hints, *res = NULL, *ai;
    char portstr[16];
    sock_t fd = SOCK_BAD;

    net_startup();
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
        if (fd == SOCK_BAD) continue;
        if (connect(fd, ai->ai_addr, (int)ai->ai_addrlen) == 0) break;
        sock_close(fd); fd = SOCK_BAD;
    }
    freeaddrinfo(res);
    if (fd == SOCK_BAD) return -1;

    { int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, SOCKOPT_CAST &one, sizeof(one)); }
    {   /* a hung response must never freeze MIDI handling: bound recv/send
         * (only bites while a request is in flight; idle keep-alive is fine) */
#ifdef _WIN32
        DWORD tmo = 3000;
#else
        struct timeval tmo; tmo.tv_sec = 3; tmo.tv_usec = 0;
#endif
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, SOCKOPT_CAST &tmo, sizeof(tmo));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, SOCKOPT_CAST &tmo, sizeof(tmo));
    }
    n->fd = (intptr_t)fd;
    return 0;
}

int tm_net_discover(int port, char *host_out, int host_cap)
{
    sock_t fd;
    struct sockaddr_in dst;
    const char *req = "TMOSC-DISCOVER?";
    int one = 1, attempt, got = -1;
#ifdef _WIN32
    DWORD tv = 800;   /* ms */
#else
    struct timeval tv; tv.tv_sec = 0; tv.tv_usec = 800000;
#endif
    if (host_cap < 8) return -1;
    net_startup();
    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd == SOCK_BAD) return -1;
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, SOCKOPT_CAST &one, sizeof(one));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, SOCKOPT_CAST &tv, sizeof(tv));

    memset(&dst, 0, sizeof(dst));
    dst.sin_family = AF_INET;
    dst.sin_port = htons((unsigned short)port);
    dst.sin_addr.s_addr = htonl(INADDR_BROADCAST);   /* 255.255.255.255 */

    for (attempt = 0; attempt < 3 && got != 0; attempt++) {
        char buf[64];
        struct sockaddr_in src;
        int n;
#ifdef _WIN32
        int slen = (int)sizeof(src);
#else
        socklen_t slen = sizeof(src);
#endif
        sendto(fd, req, (int)strlen(req), 0, (struct sockaddr *)&dst, sizeof(dst));
        for (;;) {
            slen = sizeof(src);
            n = (int)recvfrom(fd, buf, sizeof(buf) - 1, 0, (struct sockaddr *)&src, &slen);
            if (n <= 0) break;   /* timeout -> retry */
            buf[n] = '\0';
            if (strncmp(buf, "TMOSC-BRIDGE", 12) == 0) {
                if (inet_ntop(AF_INET, &src.sin_addr, host_out, host_cap)) { got = 0; break; }
            }
        }
    }
    sock_close(fd);
    return got;
}

static int write_all(sock_t fd, const char *buf, int len)
{
    int off = 0;
    while (off < len) {
        int k = (int)send(fd, buf + off, len - off, 0);
        if (k <= 0) {
#ifndef _WIN32
            if (k < 0 && errno == EINTR) continue;
#endif
            return -1;
        }
        off += k;
    }
    return 0;
}

/* Read one byte; returns 1 ok, 0 on EOF, -1 error. */
static int read_byte(sock_t fd, char *c)
{
    for (;;) {
        int k = (int)recv(fd, c, 1, 0);
        if (k == 1) return 1;
        if (k == 0) return 0;
#ifndef _WIN32
        if (errno == EINTR) continue;
#endif
        return -1;
    }
}

int tm_net_request(tm_net *n, const char *method, const char *path,
                   const char *body, char *resp, int resp_cap,
                   int *resp_len, int *status)
{
    sock_t fd;
    char req[1024];
    int blen = body ? (int)strlen(body) : 0;
    int rn, content_length = -1, i;
    char line[512];

    if (n->fd == (intptr_t)SOCK_BAD) return -1;
    fd = as_sock(n->fd);

    rn = snprintf(req, sizeof(req),
        "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: keep-alive\r\n"
        "%sContent-Length: %d\r\n\r\n",
        method, path, n->host, n->port,
        body ? "Content-Type: application/json\r\n" : "", blen);
    if (rn < 0 || rn >= (int)sizeof(req)) return -1;
    if (write_all(fd, req, rn) != 0) return -1;
    if (blen && write_all(fd, body, blen) != 0) return -1;

    /* status line */
    { int li = 0; char c;
      for (;;) {
          int r = read_byte(fd, &c);
          if (r <= 0) return -1;
          if (c == '\n') break;
          if (li < (int)sizeof(line) - 1) line[li++] = c;
      }
      line[li] = '\0';
    }
    { const char *sp = strchr(line, ' ');
      if (!sp) return -1;
      if (status) *status = atoi(sp + 1);
    }

    /* headers until blank line */
    for (;;) {
        int li = 0; char c;
        for (;;) {
            int r = read_byte(fd, &c);
            if (r <= 0) return -1;
            if (c == '\n') break;
            if (c != '\r' && li < (int)sizeof(line) - 1) line[li++] = c;
        }
        line[li] = '\0';
        if (li == 0) break;
        if (li > 15) {
            char h[16]; int k;
            for (k = 0; k < 15; k++) h[k] = (char)((line[k] >= 'A' && line[k] <= 'Z') ? line[k] + 32 : line[k]);
            h[15] = '\0';
            if (strcmp(h, "content-length:") == 0) content_length = atoi(line + 15);
        }
    }
    if (content_length < 0) return -1;

    { int got = 0;
      while (got < content_length) {
          char c; int r = read_byte(fd, &c);
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

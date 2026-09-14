/* net.c — see net.h. Cross-platform (POSIX sockets / Winsock). */
#include "net.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifdef _WIN32
  #ifndef _WIN32_WINNT
    #define _WIN32_WINNT 0x0601   /* GetAdaptersAddresses + OnLinkPrefixLength (Vista+) */
  #endif
  #include <winsock2.h>
  #include <ws2tcpip.h>
  #include <iphlpapi.h>
  #ifdef _MSC_VER
    #pragma comment(lib, "iphlpapi.lib")
  #endif
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
  #include <ifaddrs.h>
  #include <net/if.h>
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

/* --- discovery: where to send the probe ------------------------------------ */
#define DISCOVER_MAX_DST 17   /* the limited broadcast + up to 16 interfaces */

static void add_dst(struct sockaddr_in *dst, int *n, uint32_t s_addr_be, unsigned short port_be)
{
    int i;
    if (*n >= DISCOVER_MAX_DST) return;
    for (i = 0; i < *n; i++)
        if ((uint32_t)dst[i].sin_addr.s_addr == s_addr_be) return;   /* dedupe */
    memset(&dst[*n], 0, sizeof(dst[*n]));
    dst[*n].sin_family = AF_INET;
    dst[*n].sin_port = port_be;
    dst[*n].sin_addr.s_addr = s_addr_be;
    (*n)++;
}

/* The limited broadcast (255.255.255.255) leaves on ONE interface - Windows
 * picks one, Linux the default route - so a bridge on a second adapter or
 * LAN segment was never reached (#37). Add every up, non-loopback IPv4
 * interface's DIRECTED broadcast (ip | ~mask); a failure on one interface
 * just skips it. */
static void add_interface_broadcasts(struct sockaddr_in *dst, int *n, unsigned short port_be)
{
#ifdef _WIN32
    ULONG size = 16 * 1024;
    ULONG rc = (ULONG)ERROR_BUFFER_OVERFLOW;
    IP_ADAPTER_ADDRESSES *aa = NULL;
    int tries;
    for (tries = 0; tries < 3 && rc == (ULONG)ERROR_BUFFER_OVERFLOW; tries++) {
        free(aa);
        aa = (IP_ADAPTER_ADDRESSES *)malloc(size);
        if (!aa) return;
        rc = GetAdaptersAddresses(AF_INET,
                                  GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER,
                                  NULL, aa, &size);
    }
    if (rc == (ULONG)NO_ERROR) {
        IP_ADAPTER_ADDRESSES *a;
        for (a = aa; a; a = a->Next) {
            IP_ADAPTER_UNICAST_ADDRESS *u;
            if (a->OperStatus != IfOperStatusUp) continue;
            if (a->IfType == IF_TYPE_SOFTWARE_LOOPBACK) continue;
            for (u = a->FirstUnicastAddress; u; u = u->Next) {
                uint32_t ip, mask;
                int prefix = (int)u->OnLinkPrefixLength;
                if (!u->Address.lpSockaddr || u->Address.lpSockaddr->sa_family != AF_INET) continue;
                if (prefix < 1 || prefix > 30) continue;          /* /31, /32: no broadcast */
                ip = (uint32_t)((struct sockaddr_in *)u->Address.lpSockaddr)->sin_addr.s_addr;
                mask = (uint32_t)htonl(0xFFFFFFFFu << (32 - prefix));
                add_dst(dst, n, ip | ~mask, port_be);
            }
        }
    }
    free(aa);
#else
    struct ifaddrs *ifs = NULL, *ifa;
    if (getifaddrs(&ifs) != 0) return;
    for (ifa = ifs; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET) continue;
        if (!(ifa->ifa_flags & IFF_UP) || !(ifa->ifa_flags & IFF_BROADCAST)) continue;
        if (ifa->ifa_flags & IFF_LOOPBACK) continue;
        if (!ifa->ifa_broadaddr) continue;
        add_dst(dst, n, (uint32_t)((struct sockaddr_in *)ifa->ifa_broadaddr)->sin_addr.s_addr, port_be);
    }
    freeifaddrs(ifs);
#endif
}

int tm_net_discover(int port, char *host_out, int host_cap)
{
    sock_t fd;
    struct sockaddr_in dst[DISCOVER_MAX_DST];
    const char *req = "TMOSC-DISCOVER?";
    int one = 1, attempt, got = -1, ndst = 0, d;
    unsigned short port_be = htons((unsigned short)port);
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

    add_dst(dst, &ndst, (uint32_t)htonl(INADDR_BROADCAST), port_be);   /* 255.255.255.255 */
    add_interface_broadcasts(dst, &ndst, port_be);

    for (attempt = 0; attempt < 3 && got != 0; attempt++) {
        char buf[64];
        struct sockaddr_in src;
        int n;
#ifdef _WIN32
        int slen = (int)sizeof(src);
#else
        socklen_t slen = sizeof(src);
#endif
        for (d = 0; d < ndst; d++)   /* one interface failing must not stop the rest */
            sendto(fd, req, (int)strlen(req), 0, (struct sockaddr *)&dst[d], sizeof(dst[d]));
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

static char g_token[TM_NET_TOKEN_MAX];

void tm_net_set_token(const char *token)
{
    if (!token) { g_token[0] = '\0'; return; }
    strncpy(g_token, token, sizeof(g_token) - 1);
    g_token[sizeof(g_token) - 1] = '\0';
}

int tm_net_has_token(void) { return g_token[0] != '\0'; }

int tm_net_request(tm_net *n, const char *method, const char *path,
                   const char *body, char *resp, int resp_cap,
                   int *resp_len, int *status)
{
    sock_t fd;
    char req[1024 + TM_NET_TOKEN_MAX];
    char auth[TM_NET_TOKEN_MAX + 24];
    int blen = body ? (int)strlen(body) : 0;
    int rn, content_length = -1, i;
    char line[512];

    if (n->fd == (intptr_t)SOCK_BAD) return -1;
    fd = as_sock(n->fd);

    /* the bridge's opt-in token gate reads this header on every write */
    auth[0] = '\0';
    if (g_token[0]) snprintf(auth, sizeof(auth), "X-Api-Token: %s\r\n", g_token);

    rn = snprintf(req, sizeof(req),
        "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: keep-alive\r\n"
        "%s%sContent-Length: %d\r\n\r\n",
        method, path, n->host, n->port, auth,
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

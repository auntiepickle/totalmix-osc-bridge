/* tmosc_proto.c — see tmosc_proto.h. Uses only snprintf from the C stdlib. */
#include "tmosc_proto.h"
#include <stdio.h>

static float clamp01(float v)
{
    if (v < 0.0f) return 0.0f;
    if (v > 1.0f) return 1.0f;
    return v;
}

/* Append one char, tracking overflow. Returns 1 on success, 0 if full. */
static int put(char *buf, int buflen, int *n, char c)
{
    if (*n + 1 >= buflen) return 0;   /* leave room for NUL */
    buf[(*n)++] = c;
    return 1;
}

/* Append a JSON string body (contents only, no surrounding quotes), escaping
 * the characters JSON requires. Returns 1 ok, 0 overflow. */
static int put_json_escaped(char *buf, int buflen, int *n, const char *s)
{
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            if (!put(buf, buflen, n, '\\')) return 0;
            if (!put(buf, buflen, n, (char)c)) return 0;
        } else if (c == '\n') {
            if (!put(buf, buflen, n, '\\') || !put(buf, buflen, n, 'n')) return 0;
        } else if (c == '\r') {
            if (!put(buf, buflen, n, '\\') || !put(buf, buflen, n, 'r')) return 0;
        } else if (c == '\t') {
            if (!put(buf, buflen, n, '\\') || !put(buf, buflen, n, 't')) return 0;
        } else if (c < 0x20) {
            char tmp[7];
            int k;
            snprintf(tmp, sizeof(tmp), "\\u%04x", c);
            for (k = 0; tmp[k]; k++) if (!put(buf, buflen, n, tmp[k])) return 0;
        } else {
            if (!put(buf, buflen, n, (char)c)) return 0;
        }
    }
    return 1;
}

static int put_str(char *buf, int buflen, int *n, const char *s)
{
    for (; *s; s++) if (!put(buf, buflen, n, *s)) return 0;
    return 1;
}

int tm_proto_knob_json(char *buf, int buflen, const char *name, float value)
{
    int n = 0;
    char num[32];
    if (buflen <= 0) return -1;
    if (!put_str(buf, buflen, &n, "{\"type\":\"knob\",\"name\":\"")) return -1;
    if (!put_json_escaped(buf, buflen, &n, name)) return -1;
    if (!put_str(buf, buflen, &n, "\",\"value\":")) return -1;
    snprintf(num, sizeof(num), "%.6g", (double)clamp01(value));
    if (!put_str(buf, buflen, &n, num)) return -1;
    if (!put(buf, buflen, &n, '}')) return -1;
    buf[n] = '\0';
    return n;
}

/* RFC3986 unreserved set stays literal; everything else is %XX. Keeps the
 * bridge's MACRO_NAME_RE set ([A-Za-z0-9_-]) untouched and safely encodes
 * anything unexpected. */
static int is_unreserved(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
           (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~';
}

int tm_proto_trigger_path(char *buf, int buflen, const char *name)
{
    int n = 0;
    const char *s;
    if (buflen <= 0) return -1;
    if (!put_str(buf, buflen, &n, "/api/trigger/")) return -1;
    for (s = name; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (is_unreserved(c)) {
            if (!put(buf, buflen, &n, (char)c)) return -1;
        } else {
            char hex[4];
            int k;
            snprintf(hex, sizeof(hex), "%%%02X", c);
            for (k = 0; hex[k]; k++) if (!put(buf, buflen, &n, hex[k])) return -1;
        }
    }
    buf[n] = '\0';
    return n;
}

int tm_proto_trigger_body(char *buf, int buflen, float param, int clock_bpm)
{
    int n = 0;
    char num[32];
    if (buflen <= 0) return -1;
    if (!put_str(buf, buflen, &n, "{\"param\":")) return -1;
    snprintf(num, sizeof(num), "%.6g", (double)param);
    if (!put_str(buf, buflen, &n, num)) return -1;
    if (clock_bpm > 0) {
        snprintf(num, sizeof(num), ",\"clock_bpm\":%d", clock_bpm);
        if (!put_str(buf, buflen, &n, num)) return -1;
    }
    if (!put(buf, buflen, &n, '}')) return -1;
    buf[n] = '\0';
    return n;
}

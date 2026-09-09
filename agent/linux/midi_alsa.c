/* midi_alsa.c — see midi_alsa.h. Links against libasound (-lasound). */
#include "midi_alsa.h"
#include <alsa/asoundlib.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

int tm_midi_alsa_list(tm_midi_port *out, int max)
{
    int card = -1, n = 0;
    while (n < max && snd_card_next(&card) >= 0 && card >= 0) {
        snd_ctl_t *ctl;
        char hwname[32];
        int dev = -1;
        snprintf(hwname, sizeof(hwname), "hw:%d", card);
        if (snd_ctl_open(&ctl, hwname, 0) < 0) continue;
        while (n < max && snd_ctl_rawmidi_next_device(ctl, &dev) >= 0 && dev >= 0) {
            snd_rawmidi_info_t *info;
            const char *nm;
            snd_rawmidi_info_alloca(&info);
            snd_rawmidi_info_set_device(info, (unsigned)dev);
            snd_rawmidi_info_set_stream(info, SND_RAWMIDI_STREAM_INPUT);
            snd_rawmidi_info_set_subdevice(info, 0);
            if (snd_ctl_rawmidi_info(ctl, info) < 0) continue;   /* no input here */
            nm = snd_rawmidi_info_get_name(info);
            snprintf(out[n].port, sizeof(out[n].port), "hw:%d,%d", card, dev);
            snprintf(out[n].name, sizeof(out[n].name), "%s", nm ? nm : "");
            n++;
        }
        snd_ctl_close(ctl);
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

int tm_midi_alsa_resolve(const char *query, char *out, int outlen)
{
    tm_midi_port ports[32];
    int cnt, i;
    if (query && strncmp(query, "hw:", 3) == 0) {
        snprintf(out, (size_t)outlen, "%s", query);
        return 0;
    }
    cnt = tm_midi_alsa_list(ports, 32);
    if (cnt <= 0) return -1;
    if (query && *query) {
        for (i = 0; i < cnt; i++) {
            if (ci_contains(ports[i].name, query)) {
                snprintf(out, (size_t)outlen, "%s", ports[i].port);
                return 0;
            }
        }
        return -1;
    }
    snprintf(out, (size_t)outlen, "%s", ports[0].port);   /* first input */
    return 0;
}

struct tm_midi_alsa {
    snd_rawmidi_t *in;
    int nfds;
};

tm_midi_alsa *tm_midi_alsa_open(const char *device)
{
    tm_midi_alsa *m = (tm_midi_alsa *)calloc(1, sizeof(*m));
    if (!m) return NULL;
    if (snd_rawmidi_open(&m->in, NULL, device, SND_RAWMIDI_NONBLOCK) < 0) {
        free(m);
        return NULL;
    }
    m->nfds = snd_rawmidi_poll_descriptors_count(m->in);
    return m;
}

void tm_midi_alsa_close(tm_midi_alsa *m)
{
    if (!m) return;
    if (m->in) snd_rawmidi_close(m->in);
    free(m);
}

int tm_midi_alsa_pollfds(tm_midi_alsa *m, struct pollfd *pfds, int max)
{
    if (m->nfds > max) return -1;
    return snd_rawmidi_poll_descriptors(m->in, pfds, (unsigned)max);
}

int tm_midi_alsa_read(tm_midi_alsa *m, unsigned char *buf, int cap)
{
    ssize_t k = snd_rawmidi_read(m->in, buf, (size_t)cap);
    if (k == -EAGAIN) return 0;
    if (k < 0) return -1;
    return (int)k;
}

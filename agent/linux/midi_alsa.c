/* midi_alsa.c — see midi_alsa.h. Links against libasound (-lasound). */
#include "midi_alsa.h"
#include <alsa/asoundlib.h>
#include <stdlib.h>

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

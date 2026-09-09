/* tmosc_proto.h — build the bridge wire messages (freestanding base).
 *
 * The agent drives the EXISTING bridge through the same two entry points the
 * browser uses, so the server needs no changes:
 *   - knob stream : WebSocket text frame  {"type":"knob","name":..,"value":..}
 *   - macro fire  : HTTP POST /api/trigger/<name>  body {"param":..[, "clock_bpm":..]}
 *
 * These builders write into caller-provided buffers (no malloc) and return the
 * byte length written, or -1 if it would not fit. Names are escaped/encoded.
 */
#ifndef TMOSC_PROTO_H
#define TMOSC_PROTO_H

#ifdef __cplusplus
extern "C" {
#endif

/* {"type":"knob","name":"<name>","value":<v>}  -> buf. value clamped 0..1. */
int tm_proto_knob_json(char *buf, int buflen, const char *name, float value);

/* "/api/trigger/<url-encoded name>" -> buf. */
int tm_proto_trigger_path(char *buf, int buflen, const char *name);

/* {"param":<v>} or {"param":<v>,"clock_bpm":<bpm>} (bpm included only if >0). */
int tm_proto_trigger_body(char *buf, int buflen, float param, int clock_bpm);

#ifdef __cplusplus
}
#endif
#endif /* TMOSC_PROTO_H */

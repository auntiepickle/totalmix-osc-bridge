"""Generate tray.ico for the Windows agent — a clean indigo knob with a pointer.

Pure-stdlib: renders each size at 4x and box-downsamples (premultiplied alpha)
for crisp edges, then writes a multi-size .ico of 32-bit BGRA BMP images.
Run: python make_icon.py   (writes tray.ico next to this file)
"""
import math, struct, os

# app accent palette
BODY   = (79, 70, 229)     # indigo #4F46E5 — normal / running
BODY_ERR = (234, 88, 12)   # orange #EA580C — MIDI unavailable / retrying
POINT  = (240, 244, 255)   # near-white pointer
SS     = 4                 # supersample factor
ANGLE  = math.radians(30)  # pointer at ~1 o'clock (30deg right of up)


def render(size, body=BODY):
    rim = tuple(int(c * 0.38) for c in body)   # darker shade of the body
    S = size * SS
    cx = cy = (S - 1) / 2.0
    R = S * 0.46
    rim_w = S * 0.09
    dirx, diry = math.sin(ANGLE), -math.cos(ANGLE)
    p0 = 0.06 * R                 # pointer starts near centre
    p1 = 0.74 * R                 # pointer reaches toward the rim
    pw = S * 0.085               # pointer half width
    px = [[(0, 0, 0, 0)] * S for _ in range(S)]
    for y in range(S):
        for x in range(S):
            dx, dy = x - cx, y - cy
            d = math.hypot(dx, dy)
            col = None
            if d <= R:
                col = rim if d >= R - rim_w else body
            # pointer segment (project onto the pointer direction)
            t = dx * dirx + dy * diry
            if p0 <= t <= p1:
                perp = abs(dx * (-diry) + dy * dirx)
                if perp <= pw:
                    col = POINT
            if col is not None:
                px[y][x] = (col[0], col[1], col[2], 255)
    # box downsample with premultiplied alpha
    out = bytearray()
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            r = g = b = a = 0
            for j in range(SS):
                for i in range(SS):
                    pr, pg, pb, pa = px[y * SS + j][x * SS + i]
                    r += pr * pa; g += pg * pa; b += pb * pa; a += pa
            n = SS * SS
            aa = a // n
            if aa == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((r // a, g // a, b // a, aa))
        rows.append(row)
    return rows


def bmp_image(rows, size):
    # BITMAPINFOHEADER: height doubled (XOR image + AND mask)
    hdr = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):          # bottom-up
        for x in range(size):
            r, g, b, a = rows[y][x]
            xor += bytes((b, g, r, a))          # BGRA
    # AND mask: 1bpp, bottom-up, rows padded to 4 bytes. Bit=1 => transparent.
    # Derive from alpha so fully-transparent pixels cut out even where a
    # renderer ignores the 32-bit alpha channel.
    and_row = ((size + 31) // 32) * 4
    andmask = bytearray()
    for y in range(size - 1, -1, -1):
        bits = bytearray(and_row)
        for x in range(size):
            if rows[y][x][3] == 0:
                bits[x // 8] |= (0x80 >> (x % 8))
        andmask += bits
    return hdr + bytes(xor) + bytes(andmask)


def write_ico(path, body):
    sizes = [16, 24, 32, 48]
    images = [(s, bmp_image(render(s, body), s)) for s in sizes]
    n = len(images)
    dirsz = 6 + 16 * n
    with open(path, 'wb') as f:
        f.write(struct.pack('<HHH', 0, 1, n))   # ICONDIR
        offset = dirsz
        for s, data in images:
            w = 0 if s >= 256 else s
            f.write(struct.pack('<BBBBHHII', w, w, 0, 0, 1, 32, len(data), offset))
            offset += len(data)
        for _, data in images:
            f.write(data)
    print(f"wrote {path} ({os.path.getsize(path)} bytes, sizes {sizes})")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    write_ico(os.path.join(here, 'tray.ico'), BODY)          # normal / running
    write_ico(os.path.join(here, 'tray_err.ico'), BODY_ERR)  # MIDI unavailable / retrying


if __name__ == '__main__':
    main()

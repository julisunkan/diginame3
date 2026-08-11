"""
PWA Icon Generator for otherapps sub-applications.
Run: python otherapps/generate_icons.py
Generates 192x192 and 512x512 PNG icons (regular + maskable) for each app.
"""
import os, math
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(__file__)

APPS = {
    'docsformatter': {
        'colors': ('#1d4ed8', '#2563EB', '#93c5fd'),
        'symbol': 'doc',
        'label': 'DF',
    },
    'meetingsummarizer': {
        'colors': ('#6d28d9', '#7C3AED', '#c4b5fd'),
        'symbol': 'mic',
        'label': 'MS',
    },
    'onlineidval': {
        'colors': ('#047857', '#059669', '#6ee7b7'),
        'symbol': 'shield',
        'label': 'ID',
    },
    'csvany': {
        'colors': ('#b45309', '#D97706', '#fcd34d'),
        'symbol': 'csv',
        'label': 'CA',
    },
    'actibook': {
        'colors': ('#b91c1c', '#DC2626', '#fca5a5'),
        'symbol': 'book',
        'label': 'AB',
    },
}


def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def gradient_bg(size, color1, color2):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = img.load()
    c1 = hex_to_rgb(color1)
    c2 = hex_to_rgb(color2)
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            c = lerp_color(c1, c2, t)
            pixels[x, y] = (*c, 255)
    return img


def draw_symbol(draw, size, symbol, accent_color):
    cx, cy = size / 2, size / 2
    p = size / 192  # scale factor (designed at 192px)
    ac = hex_to_rgb(accent_color)
    white = (255, 255, 255, 240)
    light = (*ac, 180)

    if symbol == 'doc':
        # Document with lines
        w, h = 90 * p, 110 * p
        x0, y0 = cx - w / 2, cy - h / 2
        # page body
        draw.rounded_rectangle([x0, y0, x0+w, y0+h], radius=8*p, fill=(255,255,255,230))
        # fold corner
        fold = 22 * p
        draw.polygon([(x0+w-fold, y0), (x0+w, y0+fold), (x0+w-fold, y0+fold)], fill=(*ac, 180))
        # lines
        for i in range(4):
            lx0 = x0 + 14*p
            lx1 = x0 + w - 14*p - (fold if i == 0 else 0)
            ly  = y0 + (32 + i * 18) * p
            draw.rectangle([lx0, ly, lx1, ly + 4*p], fill=(*ac, 160))

    elif symbol == 'mic':
        # Microphone
        mr = 22 * p
        mh = 48 * p
        mx0, my0 = cx - mr, cy - mh / 2 - 8*p
        draw.rounded_rectangle([mx0, my0, mx0+2*mr, my0+mh], radius=mr, fill=white)
        # stand
        sr = 40 * p
        draw.arc([cx-sr, cy, cx+sr, cy+sr*1.4], start=180, end=360, fill=white, width=int(6*p))
        draw.rectangle([cx-3*p, cy+sr*0.7, cx+3*p, cy+sr*1.4+6*p], fill=white)
        draw.rectangle([cx-14*p, cy+sr*1.4+4*p, cx+14*p, cy+sr*1.4+10*p], fill=white)

    elif symbol == 'shield':
        # Shield shape
        sh = 100 * p
        sw = 80 * p
        sx0, sy0 = cx - sw/2, cy - sh/2
        pts = [
            (cx, sy0),
            (sx0+sw, sy0+sh*0.25),
            (sx0+sw, sy0+sh*0.65),
            (cx, sy0+sh),
            (sx0, sy0+sh*0.65),
            (sx0, sy0+sh*0.25),
        ]
        draw.polygon(pts, fill=white)
        # checkmark
        ck_pts = [
            (cx - 18*p, cy),
            (cx - 6*p, cy + 16*p),
            (cx + 20*p, cy - 14*p),
        ]
        draw.line(ck_pts, fill=(*ac, 230), width=int(10*p), joint='curve')

    elif symbol == 'csv':
        # Grid/table icon
        cols, rows = 3, 3
        cw = 28 * p
        pad = 2 * p
        total_w = cols * cw + (cols-1) * pad
        total_h = rows * cw + (rows-1) * pad
        x0 = cx - total_w / 2
        y0 = cy - total_h / 2
        for r in range(rows):
            for c in range(cols):
                rx = x0 + c * (cw + pad)
                ry = y0 + r * (cw + pad)
                fill = white if (r == 0 or c == 0) else (255, 255, 255, 140)
                draw.rounded_rectangle([rx, ry, rx+cw, ry+cw], radius=3*p, fill=fill)
        # arrow overlay
        arr_y = cy + total_h/2 + 12*p
        draw.polygon([
            (cx, arr_y + 14*p),
            (cx - 14*p, arr_y),
            (cx + 14*p, arr_y),
        ], fill=(255, 255, 255, 200))

    elif symbol == 'book':
        # Open book with star burst
        bw = 100 * p
        bh = 70 * p
        bx0, by0 = cx - bw/2, cy - bh/2 + 10*p
        # left page
        draw.polygon([
            (bx0, by0+8*p),
            (cx-3*p, by0),
            (cx-3*p, by0+bh),
            (bx0, by0+bh-6*p),
        ], fill=white)
        # right page
        draw.polygon([
            (cx+3*p, by0),
            (bx0+bw, by0+8*p),
            (bx0+bw, by0+bh-6*p),
            (cx+3*p, by0+bh),
        ], fill=(255, 255, 255, 210))
        # spine
        draw.rectangle([cx-3*p, by0, cx+3*p, by0+bh], fill=(*ac, 200))
        # star above
        _draw_star(draw, cx, by0 - 22*p, 16*p, 5, (255, 230, 80, 230))


def _draw_star(draw, cx, cy, r, points, color):
    verts = []
    for i in range(points * 2):
        angle  = math.pi * i / points - math.pi / 2
        radius = r if i % 2 == 0 else r * 0.4
        verts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(verts, fill=color)


def make_icon(size, app_key, maskable=False):
    cfg      = APPS[app_key]
    c1, c2, accent = cfg['colors']
    bg       = gradient_bg(size, c1, c2)

    if maskable:
        canvas     = bg
        inner_size = int(size * 0.75)
        inner      = make_icon(inner_size, app_key, maskable=False)
        offset     = (size - inner_size) // 2
        canvas.paste(inner, (offset, offset), inner)
        return canvas

    # rounded-rect mask
    mask      = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    radius    = size // 5
    mask_draw.rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    bg.putalpha(mask)

    draw = ImageDraw.Draw(bg)
    draw_symbol(draw, size, cfg['symbol'], accent)
    return bg


def generate_all():
    for app_key in APPS:
        icons_dir = os.path.join(BASE_DIR, app_key, 'static', 'icons')
        os.makedirs(icons_dir, exist_ok=True)
        for size in (192, 512):
            icon = make_icon(size, app_key, maskable=False)
            path = os.path.join(icons_dir, f'icon-{size}.png')
            icon.save(path, 'PNG')
            print(f'  ✓ {path}')
            icon_m = make_icon(size, app_key, maskable=True)
            path_m = os.path.join(icons_dir, f'icon-{size}-maskable.png')
            icon_m.save(path_m, 'PNG')
            print(f'  ✓ {path_m}')
    print('All otherapps icons generated!')


if __name__ == '__main__':
    generate_all()

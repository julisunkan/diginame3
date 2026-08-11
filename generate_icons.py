"""
PWA Icon Generator — colorful gradient icons for Blog CMS.
Run:  python3 generate_icons.py
"""
import os
import math
from PIL import Image, ImageDraw

ICONS_DIR = 'static/icons'
os.makedirs(ICONS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


# ---------------------------------------------------------------------------
# Background: three-stop diagonal gradient  (indigo → violet → coral)
# ---------------------------------------------------------------------------
STOP0 = hex_to_rgb('#4f46e5')   # indigo
STOP1 = hex_to_rgb('#a855f7')   # violet
STOP2 = hex_to_rgb('#f43f5e')   # rose/coral


def gradient_bg(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            if t < 0.5:
                c = lerp_color(STOP0, STOP1, t * 2)
            else:
                c = lerp_color(STOP1, STOP2, (t - 0.5) * 2)
            pixels[x, y] = (*c, 255)
    return img


# ---------------------------------------------------------------------------
# Draw the icon artwork on top of the background
# ---------------------------------------------------------------------------

def draw_icon_art(draw, size):
    """
    Design: stylised open book with a glowing star burst above it.
    All coordinates are proportional to `size`.
    """
    p = size / 512   # scale factor (design was made at 512 px)

    cx = size / 2
    cy = size / 2

    # ---- outer glow ring -------------------------------------------------
    r_outer = 190 * p
    glow_bbox = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]
    draw.ellipse(glow_bbox, fill=(255, 255, 255, 30))

    r_inner = 170 * p
    ring_bbox = [cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner]
    draw.ellipse(ring_bbox, fill=(255, 255, 255, 20))

    # ---- open book -------------------------------------------------------
    book_w  = 280 * p
    book_h  = 200 * p
    book_x  = cx - book_w / 2
    book_y  = cy - book_h / 2 + 20 * p
    spine_x = cx

    # left page
    left_pts = [
        (book_x,               book_y + 20 * p),
        (spine_x - 6 * p,      book_y),
        (spine_x - 6 * p,      book_y + book_h),
        (book_x,               book_y + book_h - 10 * p),
    ]
    draw.polygon(left_pts, fill=(255, 255, 255, 230))

    # right page
    right_pts = [
        (spine_x + 6 * p,      book_y),
        (book_x + book_w,      book_y + 20 * p),
        (book_x + book_w,      book_y + book_h - 10 * p),
        (spine_x + 6 * p,      book_y + book_h),
    ]
    draw.polygon(right_pts, fill=(255, 255, 255, 210))

    # spine shadow
    draw.rectangle(
        [spine_x - 6 * p, book_y, spine_x + 6 * p, book_y + book_h],
        fill=(180, 160, 240, 220)
    )

    # lines on left page
    line_color = (120, 90, 200, 180)
    lw = max(2, int(4 * p))
    for i in range(3):
        ly = book_y + (50 + i * 40) * p
        draw.line(
            [(book_x + 20 * p, ly), (spine_x - 20 * p, ly)],
            fill=line_color, width=lw
        )

    # lines on right page
    for i in range(3):
        ly = book_y + (50 + i * 40) * p
        draw.line(
            [(spine_x + 20 * p, ly), (book_x + book_w - 20 * p, ly)],
            fill=line_color, width=lw
        )

    # ---- graduation cap above book ---------------------------------------
    cap_cx = cx
    cap_cy = book_y - 55 * p
    cap_w  = 180 * p
    cap_h  = 22 * p

    # mortarboard flat top (diamond rotated 45°)
    half = cap_w / 2
    diamond = [
        (cap_cx,         cap_cy - cap_h),
        (cap_cx + half,  cap_cy),
        (cap_cx,         cap_cy + cap_h * 0.4),
        (cap_cx - half,  cap_cy),
    ]
    draw.polygon(diamond, fill=(255, 220, 80, 245))
    draw.polygon(diamond, outline=(255, 180, 40, 200), width=max(2, int(3 * p)))

    # cap stem
    stem_w = 14 * p
    stem_h = 36 * p
    draw.rectangle(
        [cap_cx - stem_w / 2, cap_cy,
         cap_cx + stem_w / 2, cap_cy + stem_h],
        fill=(255, 220, 80, 240)
    )

    # tassel
    tassel_x = cap_cx + half * 0.6
    tassel_y = cap_cy
    draw.line(
        [(tassel_x, tassel_y), (tassel_x + 18 * p, tassel_y + 38 * p)],
        fill=(255, 180, 40, 230), width=max(2, int(3 * p))
    )
    r_t = 10 * p
    draw.ellipse(
        [tassel_x + 18 * p - r_t, tassel_y + 38 * p - r_t,
         tassel_x + 18 * p + r_t, tassel_y + 38 * p + r_t],
        fill=(255, 200, 60, 240)
    )

    # ---- small sparkle stars around the cap -----------------------------
    star_positions = [
        (cx - 140 * p, cy - 160 * p, 10 * p),
        (cx + 150 * p, cy - 130 * p, 8 * p),
        (cx - 170 * p, cy - 80 * p,  6 * p),
        (cx + 170 * p, cy - 60 * p,  7 * p),
        (cx + 60 * p,  cy - 190 * p, 9 * p),
    ]
    for sx, sy, sr in star_positions:
        _draw_star(draw, sx, sy, sr, 4, (255, 255, 200, 220))


def _draw_star(draw, cx, cy, r, points, color):
    """Draw a simple 4-point star."""
    verts = []
    for i in range(points * 2):
        angle = math.pi * i / points - math.pi / 2
        radius = r if i % 2 == 0 else r * 0.4
        verts.append((cx + radius * math.cos(angle),
                       cy + radius * math.sin(angle)))
    draw.polygon(verts, fill=color)


# ---------------------------------------------------------------------------
# Compose final icon
# ---------------------------------------------------------------------------

def make_icon(size, maskable=False):
    """
    maskable=True → add extra 10% padding so the safe zone is respected.
    """
    if maskable:
        # Draw at 80% scale centred on gradient background
        canvas = gradient_bg(size)
        inner_size = int(size * 0.80)
        inner = make_icon(inner_size, maskable=False)
        offset = (size - inner_size) // 2
        canvas.paste(inner, (offset, offset), inner)
        return canvas

    img = gradient_bg(size)

    # Rounded-rectangle clip mask
    mask = Image.new('L', (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    radius = size // 5
    mask_draw.rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    img.putalpha(mask)

    draw = ImageDraw.Draw(img)
    draw_icon_art(draw, size)
    return img


# ---------------------------------------------------------------------------
# Generate all sizes
# ---------------------------------------------------------------------------

REGULAR_SIZES  = [16, 32, 72, 96, 128, 144, 152, 192, 384, 512]
MASKABLE_SIZES = [192, 512]


def generate_all():
    print("Generating regular icons…")
    for s in REGULAR_SIZES:
        icon = make_icon(s)
        path = f'{ICONS_DIR}/icon-{s}x{s}.png'
        icon.save(path, 'PNG')
        print(f"  ✓ {path}")

    print("Generating maskable icons…")
    for s in MASKABLE_SIZES:
        icon = make_icon(s, maskable=True)
        path = f'{ICONS_DIR}/maskable-icon-{s}x{s}.png'
        icon.save(path, 'PNG')
        print(f"  ✓ {path}")

    print("All icons generated successfully!")


if __name__ == '__main__':
    generate_all()

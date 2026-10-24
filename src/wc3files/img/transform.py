"""Generic RGBA8 image-processing primitives for the icon-authoring workflow (resize,
alpha composite, disabled-icon generation) - the operations 'How to Make an Icon' and
the Reforged icon tutorial do by hand in an image editor."""


def resize_nearest(rgba, width, height, new_width, new_height):
    if new_width == width and new_height == height:
        return bytes(rgba)
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        src_y = min(height - 1, (y * height) // new_height)
        src_row = src_y * width * 4
        dst_row = y * new_width * 4
        for x in range(new_width):
            src_x = min(width - 1, (x * width) // new_width)
            si = src_row + src_x * 4
            di = dst_row + x * 4
            out[di:di + 4] = rgba[si:si + 4]
    return bytes(out)


def resize_box(rgba, width, height, new_width, new_height):
    """Box-filter (area-average) resize - good for shrinking (e.g. mipmap generation,
    downscaling source art to icon size); falls back to resize_nearest when upscaling
    on an axis, since averaging can't invent detail that isn't there."""
    if new_width == width and new_height == height:
        return bytes(rgba)
    if new_width > width or new_height > height:
        return resize_nearest(rgba, width, height, new_width, new_height)

    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        y0 = (y * height) // new_height
        y1 = max(y0 + 1, ((y + 1) * height) // new_height)
        dst_row = y * new_width * 4
        for x in range(new_width):
            x0 = (x * width) // new_width
            x1 = max(x0 + 1, ((x + 1) * width) // new_width)
            r = g = b = a = count = 0
            for sy in range(y0, y1):
                row = sy * width * 4
                for sx in range(x0, x1):
                    i = row + sx * 4
                    r += rgba[i]
                    g += rgba[i + 1]
                    b += rgba[i + 2]
                    a += rgba[i + 3]
                    count += 1
            di = dst_row + x * 4
            out[di] = r // count
            out[di + 1] = g // count
            out[di + 2] = b // count
            out[di + 3] = a // count
    return bytes(out)


def composite_over(base_rgba, width, height, overlay_rgba, overlay_width, overlay_height,
                    x=0, y=0):
    """Alpha-composite `overlay` on top of `base` ('source-over' blending), resizing the
    overlay to the base's size first if their dimensions differ (the usual case for
    applying a border-frame image to an icon)."""
    if (overlay_width, overlay_height) != (width, height):
        overlay_rgba = resize_nearest(overlay_rgba, overlay_width, overlay_height, width, height)

    out = bytearray(base_rgba)
    for py in range(height):
        oy = py - y
        if not (0 <= oy < height):
            continue
        for px in range(width):
            ox = px - x
            if not (0 <= ox < width):
                continue
            oi = (oy * width + ox) * 4
            fa = overlay_rgba[oi + 3]
            if fa == 0:
                continue
            di = (py * width + px) * 4
            if fa == 255:
                out[di:di + 4] = overlay_rgba[oi:oi + 4]
                continue
            fr, fg, fb = overlay_rgba[oi], overlay_rgba[oi + 1], overlay_rgba[oi + 2]
            br, bg, bb, ba = out[di], out[di + 1], out[di + 2], out[di + 3]
            out_a = fa + ba * (255 - fa) // 255
            if out_a == 0:
                out[di:di + 4] = (0, 0, 0, 0)
                continue
            out[di] = (fr * fa + br * ba * (255 - fa) // 255) // out_a
            out[di + 1] = (fg * fa + bg * ba * (255 - fa) // 255) // out_a
            out[di + 2] = (fb * fa + bb * ba * (255 - fa) // 255) // out_a
            out[di + 3] = out_a
    return bytes(out)


def make_disabled_variant(rgba, width, height, darken=0.4, desaturate=0.7):
    """Generate a WC3-style 'disabled' icon variant (the DISBTN* convention): darkened
    and desaturated, alpha untouched. `darken` and `desaturate` are 0..1 fractions
    (0 = unchanged, 1 = fully black / fully grayscale) matching the classic tutorial's
    manual recipe (Image -> Adjustments -> Hue/Saturation, then Brightness)."""
    npix = width * height
    out = bytearray(rgba)
    for i in range(npix):
        j = i * 4
        r, g, b = rgba[j], rgba[j + 1], rgba[j + 2]
        gray = int(round(0.299 * r + 0.587 * g + 0.114 * b))
        r = r + (gray - r) * desaturate
        g = g + (gray - g) * desaturate
        b = b + (gray - b) * desaturate
        r *= (1 - darken)
        g *= (1 - darken)
        b *= (1 - darken)
        out[j] = max(0, min(255, int(round(r))))
        out[j + 1] = max(0, min(255, int(round(g))))
        out[j + 2] = max(0, min(255, int(round(b))))
    return bytes(out)

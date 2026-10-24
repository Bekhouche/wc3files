"""Median-cut color quantization - reduces an RGBA image to a <=256-color RGB palette
plus one index per pixel, for BLP1's paletted mip format. Alpha is not quantized (kept
as an exact separate 8-bit plane), matching how every real BLP1 fixture stores it."""
import heapq


def _population(box):
    return sum(count for _, count in box)


def quantize_median_cut(rgba, width, height, max_colors=256):
    """Return (palette, indices): palette is a list of up to 256 (r, g, b) tuples
    (padded with (0, 0, 0) to exactly 256 entries), indices is width*height bytes."""
    npix = width * height
    counter = {}
    for i in range(npix):
        j = i * 4
        key = (rgba[j], rgba[j + 1], rgba[j + 2])
        counter[key] = counter.get(key, 0) + 1
    colors = list(counter.items())

    if len(colors) <= max_colors:
        palette = [color for color, _count in colors]
        color_to_index = {color: i for i, color in enumerate(palette)}
    else:
        heap = [(-_population(colors), 0, colors)]
        next_id = 1
        while len(heap) < max_colors:
            neg_pop, box_id, box = heapq.heappop(heap)
            if len(box) <= 1:
                heapq.heappush(heap, (neg_pop, box_id, box))
                break
            ranges = [max(c[0][ch] for c in box) - min(c[0][ch] for c in box) for ch in range(3)]
            channel = ranges.index(max(ranges))
            box.sort(key=lambda c: c[0][channel])
            half = _population(box) / 2
            cum = 0
            split_at = len(box) // 2
            for idx, (_color, count) in enumerate(box):
                cum += count
                if cum >= half:
                    split_at = idx + 1
                    break
            split_at = max(1, min(len(box) - 1, split_at))
            left, right = box[:split_at], box[split_at:]
            heapq.heappush(heap, (-_population(left), next_id, left))
            heapq.heappush(heap, (-_population(right), next_id + 1, right))
            next_id += 2

        palette = []
        color_to_index = {}
        for box_index, (_neg_pop, _box_id, box) in enumerate(heap):
            total = _population(box)
            r = sum(color[0] * count for color, count in box) / total
            g = sum(color[1] * count for color, count in box) / total
            b = sum(color[2] * count for color, count in box) / total
            palette.append((round(r), round(g), round(b)))
            for color, _count in box:
                color_to_index[color] = box_index

    while len(palette) < 256:
        palette.append((0, 0, 0))

    indices = bytearray(npix)
    for i in range(npix):
        j = i * 4
        indices[i] = color_to_index[(rgba[j], rgba[j + 1], rgba[j + 2])]

    return palette[:256], bytes(indices)

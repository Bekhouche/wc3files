"""
Minimal baseline- and progressive-JPEG decoder purpose-built for BLP1's embedded
texture streams.

BLP1's "compression=0" (JPEG) mip data is a real JPEG bitstream, but not a
photographic one: real BLP1 files sampled from this package's fixtures always encode
4 components (B, G, R, A) at 4:4:4 (no chroma subsampling), sharing one quantization
table, with no colorspace-transform marker (no Adobe APP14) - i.e. each component is
stored as an independent grayscale-style DCT plane, not YCbCr. This decoder mirrors
that: it never applies a YCbCr -> RGB color transform, it just decodes each component
to its own plane and returns them as-is; blp_file.py is responsible for mapping planes
to channels. Both baseline (SOF0/SOF1) and progressive (SOF2, spectral selection +
successive approximation) are supported - a real, meaningful fraction of real .blp
fixtures (~9%) use progressive encoding, concentrated in Reforged-era maps. Arithmetic
coding is not used by BLP1 and is not supported here - raised explicitly rather than
silently mis-decoded.
"""
import math
import struct

ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

_RST_LO, _RST_HI = 0xD0, 0xD7


class JpegError(Exception):
    pass


def _build_huffman_table(bits, values):
    table = {}
    code = 0
    k = 0
    for length in range(1, 17):
        count = bits[length - 1]
        for _ in range(count):
            table[(length, code)] = values[k]
            code += 1
            k += 1
        code <<= 1
    return table


class _BitReader:
    __slots__ = ('data', 'pos', 'bitbuf', 'bitcnt')

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcnt = 0

    def get_bit(self):
        if self.bitcnt == 0:
            self.bitbuf = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.bitcnt = 8
        self.bitcnt -= 1
        return (self.bitbuf >> self.bitcnt) & 1

    def get_bits(self, nbits):
        v = 0
        for _ in range(nbits):
            v = (v << 1) | self.get_bit()
        return v

    def receive_extend(self, s):
        if s == 0:
            return 0
        v = self.get_bits(s)
        if v < (1 << (s - 1)):
            v = v - (1 << s) + 1
        return v

    def decode_huff(self, table):
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.get_bit()
            sym = table.get((length, code))
            if sym is not None:
                return sym
        raise JpegError('bad huffman code (no match up to 16 bits)')


def _destuff(data, start):
    """Copy entropy-coded bytes from `start`, undoing 0xFF00 byte-stuffing, until a real
    marker (0xFF followed by a non-zero byte) or end of data is hit. Returns
    (destuffed_bytes, position_of_marker_0xFF_byte_or_len(data), marker_byte_or_None)."""
    out = bytearray()
    i = start
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0xFF:
            if i + 1 >= n:
                return bytes(out), i, None
            nxt = data[i + 1]
            if nxt == 0x00:
                out.append(0xFF)
                i += 2
                continue
            return bytes(out), i, nxt
        out.append(b)
        i += 1
    return bytes(out), i, None


_COS_TABLE = [[math.cos((2 * x + 1) * u * math.pi / 16) for u in range(8)] for x in range(8)]
_C = [1 / math.sqrt(2)] + [1.0] * 7


def _idct_1d(vec):
    out = [0.0] * 8
    for x in range(8):
        row = _COS_TABLE[x]
        s = 0.0
        for u in range(8):
            s += _C[u] * vec[u] * row[u]
        out[x] = s * 0.5
    return out


def _idct_block(block):
    tmp = [_idct_1d(block[r * 8:(r + 1) * 8]) for r in range(8)]
    out = [[0] * 8 for _ in range(8)]
    for c in range(8):
        col = _idct_1d([tmp[r][c] for r in range(8)])
        for r in range(8):
            v = int(round(col[r] + 128.0))
            out[r][c] = 0 if v < 0 else 255 if v > 255 else v
    return out


def _decode_block(reader, dc_table, ac_table, quant_table, dc_pred):
    t = reader.decode_huff(dc_table)
    dc = dc_pred + reader.receive_extend(t)
    zz = [0] * 64
    zz[0] = dc
    k = 1
    while k < 64:
        rs = reader.decode_huff(ac_table)
        r, s = rs >> 4, rs & 0xF
        if s == 0:
            if r == 15:
                k += 16
                continue
            break
        k += r
        if k >= 64:
            break
        zz[k] = reader.receive_extend(s)
        k += 1
    block = [0.0] * 64
    for i in range(64):
        block[ZIGZAG[i]] = zz[i] * quant_table[i]
    return block, dc


def _crop_and_upsample(buf, plane_w, comp_w, comp_h, sx, sy, width, height):
    out = bytearray(width * height)
    for y in range(height):
        src_y = min(y // sy, comp_h - 1)
        src_row = src_y * plane_w
        dst_row = y * width
        if sx == 1:
            out[dst_row:dst_row + width] = buf[src_row:src_row + width]
        else:
            for x in range(width):
                out[dst_row + x] = buf[src_row + min(x // sx, comp_w - 1)]
    return bytes(out)


def _decode_scan(data, entropy_start, width, height, components, scan_comps,
                  quant_tables, huff_dc, huff_ac, restart_interval):
    comp_by_id = {c['id']: c for c in components}
    hmax = max(c['h'] for c in components)
    vmax = max(c['v'] for c in components)
    mcus_per_line = -(-width // (8 * hmax))
    mcu_lines = -(-height // (8 * vmax))
    total_mcus = mcus_per_line * mcu_lines

    plane_w, plane_h, planes = {}, {}, {}
    for c in components:
        pw = mcus_per_line * c['h'] * 8
        ph = mcu_lines * c['v'] * 8
        plane_w[c['id']] = pw
        plane_h[c['id']] = ph
        planes[c['id']] = bytearray(pw * ph)

    scan_order = [(comp_by_id[cs], td, ta) for cs, td, ta in scan_comps]
    dc_pred = {c['id']: 0 for c in components}

    seg_bytes, marker_pos, marker = _destuff(data, entropy_start)
    reader = _BitReader(seg_bytes)

    mcu_count = 0
    while mcu_count < total_mcus:
        mcu_row, mcu_col = divmod(mcu_count, mcus_per_line)
        for comp, td, ta in scan_order:
            h_i, v_i = comp['h'], comp['v']
            qt = quant_tables[comp['qt']]
            dc_table = huff_dc[td]
            ac_table = huff_ac[ta]
            pw = plane_w[comp['id']]
            buf = planes[comp['id']]
            for by in range(v_i):
                for bx in range(h_i):
                    block, dc_pred[comp['id']] = _decode_block(
                        reader, dc_table, ac_table, qt, dc_pred[comp['id']])
                    pixels = _idct_block(block)
                    px0 = (mcu_col * h_i + bx) * 8
                    py0 = (mcu_row * v_i + by) * 8
                    for r in range(8):
                        row_off = (py0 + r) * pw + px0
                        buf[row_off:row_off + 8] = bytes(pixels[r])
        mcu_count += 1
        if restart_interval and mcu_count % restart_interval == 0 and mcu_count < total_mcus:
            if marker is None or not (_RST_LO <= marker <= _RST_HI):
                raise JpegError('expected a restart marker but did not find one')
            dc_pred = {c['id']: 0 for c in components}
            seg_bytes, marker_pos, marker = _destuff(data, marker_pos + 2)
            reader = _BitReader(seg_bytes)

    out_planes = []
    for c in components:
        sx, sy = hmax // c['h'], vmax // c['v']
        comp_w = -(-width * c['h'] // hmax)
        comp_h = -(-height * c['v'] // vmax)
        out_planes.append(_crop_and_upsample(
            planes[c['id']], plane_w[c['id']], comp_w, comp_h, sx, sy, width, height))
    return out_planes


def _decode_dc_first_value(reader, dc_table, pred):
    t = reader.decode_huff(dc_table)
    return pred + reader.receive_extend(t)


def _decode_ac_first_block(reader, ac_table, block, ss, se, al, eobrun):
    if eobrun > 0:
        return eobrun - 1
    k = ss
    while k <= se:
        rs = reader.decode_huff(ac_table)
        r, s = rs >> 4, rs & 0xF
        if s == 0:
            if r < 15:
                eobrun = (1 << r) - 1
                if r:
                    eobrun += reader.get_bits(r)
                return eobrun
            k += 16
            continue
        k += r
        if k > se:
            break
        block[ZIGZAG[k]] = reader.receive_extend(s) << al
        k += 1
    return 0


def _refine_remaining(reader, block, k, se, bit_val):
    """Apply a correction bit to every already-nonzero coefficient from k to se
    (no new coefficients placed) - used both for the tail of a block where a fresh
    EOB run was just declared, and for a whole block covered by an ongoing run."""
    while k <= se:
        idx = ZIGZAG[k]
        if block[idx] != 0:
            if reader.get_bit() and (block[idx] & bit_val) == 0:
                block[idx] += bit_val if block[idx] > 0 else -bit_val
        k += 1


def _decode_ac_refine_block(reader, ac_table, block, ss, se, al, eobrun):
    bit_val = 1 << al
    if eobrun > 0:
        # This whole block is covered by an already-declared run from an earlier
        # block - no new coefficients, just refine everything and count it off.
        _refine_remaining(reader, block, ss, se, bit_val)
        return eobrun - 1

    k = ss
    while k <= se:
        rs = reader.decode_huff(ac_table)
        r, s = rs >> 4, rs & 0xF
        new_coeff = 0
        if s == 0:
            if r != 15:
                # Fresh EOB declared mid-block: the encoded run count already
                # includes this block, so refine this block's own tail now and
                # return the count pre-decremented for the *next* block - do not
                # decrement again here.
                run = (1 << r) - 1
                if r:
                    run += reader.get_bits(r)
                _refine_remaining(reader, block, k, se, bit_val)
                return run
            # r == 15: ZRL - skip 16 zero-history coefficients (refining any
            # nonzero-history ones passed over), no new coefficient this time.
        else:
            new_coeff = bit_val if reader.get_bit() else -bit_val
        while k <= se:
            idx = ZIGZAG[k]
            if block[idx] != 0:
                if reader.get_bit() and (block[idx] & bit_val) == 0:
                    block[idx] += bit_val if block[idx] > 0 else -bit_val
            else:
                if r == 0:
                    break
                r -= 1
            k += 1
        if s != 0 and k <= se:
            block[ZIGZAG[k]] = new_coeff
        k += 1
    return 0


def _decode_progressive_scan(data, entropy_start, hmax, vmax, mcus_per_line, mcu_lines,
                              comp_state, components_by_id, scan_comps, ss, se, ah, al,
                              huff_dc, huff_ac, restart_interval):
    """Decode one progressive-JPEG scan into the persistent per-component coefficient
    state (mutated in place). Returns the byte offset of the marker that ended this
    scan's entropy-coded segment, so the caller can resume marker parsing there."""
    interleaved = len(scan_comps) > 1
    seg_bytes, marker_pos, marker = _destuff(data, entropy_start)
    reader = _BitReader(seg_bytes)
    eobrun = 0
    predictors = {cs: 0 for cs, _td, _ta in scan_comps}

    def restart(unit_index):
        nonlocal seg_bytes, marker_pos, marker, reader, eobrun, predictors
        if not restart_interval or unit_index % restart_interval != 0:
            return
        if marker is None or not (_RST_LO <= marker <= _RST_HI):
            raise JpegError('expected a restart marker but did not find one')
        predictors = {cs: 0 for cs, _td, _ta in scan_comps}
        eobrun = 0
        seg_bytes, marker_pos, marker = _destuff(data, marker_pos + 2)
        reader = _BitReader(seg_bytes)

    if interleaved:  # only ever a DC scan (Ss == Se == 0), per the JPEG spec
        total_mcus = mcus_per_line * mcu_lines
        for mcu_index in range(total_mcus):
            mcu_row, mcu_col = divmod(mcu_index, mcus_per_line)
            for cs, td, _ta in scan_comps:
                comp = components_by_id[cs]
                state = comp_state[cs]
                for by in range(comp['v']):
                    for bx in range(comp['h']):
                        br = mcu_row * comp['v'] + by
                        bc = mcu_col * comp['h'] + bx
                        block = state['blocks'][br * state['blocks_per_line'] + bc]
                        if ah == 0:
                            predictors[cs] = _decode_dc_first_value(reader, huff_dc[td], predictors[cs])
                            block[0] = predictors[cs] << al
                        elif reader.get_bit():
                            block[0] |= (1 << al)
            if mcu_index + 1 < total_mcus:
                restart(mcu_index + 1)
    else:
        cs, td, ta = scan_comps[0]
        comp = components_by_id[cs]
        state = comp_state[cs]
        blocks_w, blocks_h = state['real_blocks_w'], state['real_blocks_h']
        total_blocks = blocks_w * blocks_h
        is_dc = ss == 0
        for block_index in range(total_blocks):
            br, bc = divmod(block_index, blocks_w)
            block = state['blocks'][br * state['blocks_per_line'] + bc]
            if is_dc:
                if ah == 0:
                    predictors[cs] = _decode_dc_first_value(reader, huff_dc[td], predictors[cs])
                    block[0] = predictors[cs] << al
                elif reader.get_bit():
                    block[0] |= (1 << al)
            elif ah == 0:
                eobrun = _decode_ac_first_block(reader, huff_ac[ta], block, ss, se, al, eobrun)
            else:
                eobrun = _decode_ac_refine_block(reader, huff_ac[ta], block, ss, se, al, eobrun)
            if block_index + 1 < total_blocks:
                restart(block_index + 1)
    return marker_pos


def _finalize_progressive(comp_state, components, quant_tables, hmax, vmax, width, height):
    out_planes = []
    for c in components:
        state = comp_state[c['id']]
        pw, ph = state['blocks_per_line'] * 8, state['blocks_per_col'] * 8
        buf = bytearray(pw * ph)
        qt = quant_tables[c['qt']]
        for br in range(state['blocks_per_col']):
            for bc in range(state['blocks_per_line']):
                # Unlike the baseline decoder's raw Huffman output, this array is
                # already stored in natural order (the progressive AC decoders write
                # via block[ZIGZAG[k]] directly) - only the quant table itself is
                # still zigzag-ordered (as read straight from the DQT segment).
                coeffs = state['blocks'][br * state['blocks_per_line'] + bc]
                block = [0.0] * 64
                for i in range(64):
                    pos = ZIGZAG[i]
                    block[pos] = coeffs[pos] * qt[i]
                pixels = _idct_block(block)
                px0, py0 = bc * 8, br * 8
                for r in range(8):
                    row_off = (py0 + r) * pw + px0
                    buf[row_off:row_off + 8] = bytes(pixels[r])
        sx, sy = hmax // c['h'], vmax // c['v']
        comp_w = -(-width * c['h'] // hmax)
        comp_h = -(-height * c['v'] // vmax)
        out_planes.append(_crop_and_upsample(bytes(buf), pw, comp_w, comp_h, sx, sy, width, height))
    return out_planes


def decode_planes(data):
    """Decode a baseline- or progressive-JPEG bitstream into (width, height, planes) -
    one raw 8-bit plane (width*height bytes, row-major) per component, in the JPEG's
    own component order, with NO colorspace transform applied (see module docstring)."""
    if data[0:2] != b'\xff\xd8':
        raise JpegError('not a JPEG stream (missing SOI marker)')
    pos = 2
    n = len(data)
    quant_tables = {}
    huff_dc = {}
    huff_ac = {}
    width = height = None
    components = []
    components_by_id = {}
    restart_interval = 0
    progressive = None
    hmax = vmax = mcus_per_line = mcu_lines = None
    comp_state = None

    while pos < n - 1:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0xD8, 0x01) or _RST_LO <= marker <= _RST_HI:
            continue
        if marker == 0xD9:  # EOI
            if progressive and comp_state is not None:
                return width, height, _finalize_progressive(
                    comp_state, components, quant_tables, hmax, vmax, width, height)
            break
        seglen = struct.unpack_from('>H', data, pos)[0]
        seg = data[pos + 2:pos + seglen]

        if marker == 0xDB:  # DQT
            p = 0
            while p < len(seg):
                pq_tq = seg[p]
                p += 1
                pq, tq = pq_tq >> 4, pq_tq & 0xF
                if pq == 0:
                    table = list(seg[p:p + 64])
                    p += 64
                else:
                    table = list(struct.unpack_from('>64H', seg, p))
                    p += 128
                quant_tables[tq] = table
        elif marker == 0xC4:  # DHT
            p = 0
            while p < len(seg):
                tc_th = seg[p]
                p += 1
                tc, th = tc_th >> 4, tc_th & 0xF
                bits = list(seg[p:p + 16])
                p += 16
                total = sum(bits)
                values = list(seg[p:p + total])
                p += total
                table = _build_huffman_table(bits, values)
                (huff_dc if tc == 0 else huff_ac)[th] = table
        elif marker in (0xC0, 0xC1, 0xC2):  # SOF0 baseline / SOF1 ext-sequential / SOF2 progressive
            progressive = marker == 0xC2
            height, width = struct.unpack_from('>HH', seg, 1)
            nc = seg[5]
            components = []
            p = 6
            for _ in range(nc):
                cid, samp, qt = seg[p], seg[p + 1], seg[p + 2]
                components.append({'id': cid, 'h': samp >> 4, 'v': samp & 0xF, 'qt': qt})
                p += 3
            components_by_id = {c['id']: c for c in components}
            hmax = max(c['h'] for c in components)
            vmax = max(c['v'] for c in components)
            mcus_per_line = -(-width // (8 * hmax))
            mcu_lines = -(-height // (8 * vmax))
            if progressive:
                comp_state = {}
                for c in components:
                    blocks_per_line = mcus_per_line * c['h']
                    blocks_per_col = mcu_lines * c['v']
                    comp_w = -(-width * c['h'] // hmax)
                    comp_h = -(-height * c['v'] // vmax)
                    comp_state[c['id']] = {
                        'blocks_per_line': blocks_per_line, 'blocks_per_col': blocks_per_col,
                        'real_blocks_w': -(-comp_w // 8), 'real_blocks_h': -(-comp_h // 8),
                        'blocks': [[0] * 64 for _ in range(blocks_per_line * blocks_per_col)],
                    }
        elif 0xC3 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            raise JpegError(f'unsupported JPEG mode (marker 0x{marker:02X}) - only baseline '
                             '(SOF0), extended-sequential (SOF1), and progressive (SOF2) '
                             'are supported')
        elif marker == 0xDD:  # DRI
            restart_interval = struct.unpack_from('>H', seg, 0)[0]
        elif marker == 0xDA:  # SOS
            if width is None:
                raise JpegError('SOS marker seen before SOF')
            ns = seg[0]
            scan_comps = []
            p = 1
            for _ in range(ns):
                cs, td_ta = seg[p], seg[p + 1]
                scan_comps.append((cs, td_ta >> 4, td_ta & 0xF))
                p += 2
            ss, se, ah_al = seg[p], seg[p + 1], seg[p + 2]
            ah, al = ah_al >> 4, ah_al & 0xF
            entropy_start = pos + seglen

            if not progressive:
                return width, height, _decode_scan(
                    data, entropy_start, width, height, components, scan_comps,
                    quant_tables, huff_dc, huff_ac, restart_interval)

            pos = _decode_progressive_scan(
                data, entropy_start, hmax, vmax, mcus_per_line, mcu_lines, comp_state,
                components_by_id, scan_comps, ss, se, ah, al, huff_dc, huff_ac, restart_interval)
            continue
        pos += seglen

    raise JpegError('reached end of data without finding an SOS/EOI marker')

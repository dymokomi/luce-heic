"""A minimal HEIF writer for building test files from raw HEVC streams.

It wraps Annex B HEVC (as encoders write it) into an ISO BMFF HEIF container with the
item layouts the decoder must handle: single coded items, grids of coded tiles, an
auxiliary alpha item, clean aperture, rotation and mirroring, and every iloc version and
field size. Python standard library only.
"""
import struct


def box(kind, payload):
    return struct.pack('>I', 8 + len(payload)) + kind + payload


def full_box(kind, version, flags, payload):
    return box(kind, bytes([version]) + flags.to_bytes(3, 'big') + payload)


def nal_units(stream):
    """The NAL units of an Annex B byte stream, without start codes."""
    units, i, start = [], 0, None
    while i + 3 <= len(stream):
        if stream[i:i + 3] == b'\x00\x00\x01':
            if start is not None:
                units.append(stream[start:i].rstrip(b'\x00'))
            i += 3
            start = i
        else:
            i += 1
    if start is not None:
        units.append(stream[start:])
    return [u for u in units if u]


def nal_type(unit):
    return (unit[0] >> 1) & 63


def hvcc(units):
    """An hvcC body for the stream's VPS, SPS and PPS; 4-byte NAL lengths."""
    sps = next(u for u in units if nal_type(u) == 33)
    rbsp = bytearray()
    zeros = 0
    for b in sps[2:]:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        rbsp.append(b)
        zeros = zeros + 1 if b == 0 else 0
    ptl = bytes(rbsp[1:13])
    chroma, depth = sps_format(bytes(rbsp))
    out = bytearray([1]) + ptl + bytes([0xf0, 0x00, 0xfc, 0xfc | chroma, 0xf8 | (depth - 8), 0xf8 | (depth - 8), 0, 0, 0x0f])
    arrays = [[u for u in units if nal_type(u) == t] for t in (32, 33, 34)]
    out.append(sum(1 for a in arrays if a))
    for t, a in zip((32, 33, 34), arrays):
        if not a:
            continue
        out += bytes([0x80 | t]) + struct.pack('>H', len(a))
        for u in a:
            out += struct.pack('>H', len(u)) + u
    return bytes(out)


class Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def bit(self):
        v = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return v

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self):
        z = 0
        while self.bit() == 0:
            z += 1
        return (1 << z) - 1 + self.bits(z)


def sps_format(rbsp):
    """chroma_format_idc and luma bit depth from an SPS RBSP (no sub-layers assumed)."""
    b = Bits(rbsp)
    b.bits(4)
    sub = b.bits(3)
    b.bits(1)
    b.bits(96)
    flags = [b.bits(2) for _ in range(sub)]
    if sub:
        b.bits(2 * (8 - sub))
    for f in flags:
        if f & 2:
            b.bits(88)
        if f & 1:
            b.bits(8)
    b.ue()
    chroma = b.ue()
    if chroma == 3:
        b.bit()
    b.ue()
    b.ue()
    if b.bit():
        for _ in range(4):
            b.ue()
    return chroma, b.ue() + 8


def sample(units):
    """The item data: every non-parameter-set NAL unit with a 4-byte length."""
    return b''.join(struct.pack('>I', len(u)) + u for u in units if nal_type(u) not in (32, 33, 34))


class Item:
    def __init__(self, kind, data, properties, hidden=False):
        self.kind, self.data, self.properties, self.hidden = kind, data, properties, hidden
        self.id = 0


def pixi(channels, depth):
    return full_box(b'pixi', 0, 0, bytes([channels] + [depth] * channels))


def ispe(width, height):
    return full_box(b'ispe', 0, 0, struct.pack('>II', width, height))


def irot(turns):
    return box(b'irot', bytes([turns]))


def imir(axis):
    return box(b'imir', bytes([axis]))


def clap(width, height, horizontal=(0, 1), vertical=(0, 1)):
    return box(b'clap', struct.pack('>IIIIiIiI', width, 1, height, 1, horizontal[0], horizontal[1], vertical[0], vertical[1]))


def nclx(matrix, full_range, primaries=1, transfer=13):
    return box(b'colr', b'nclx' + struct.pack('>HHHB', primaries, transfer, matrix, 0x80 if full_range else 0))


def auxc_alpha():
    return full_box(b'auxC', 0, 0, b'urn:mpeg:hevc:2015:auxid:1\x00')


def write(items, primary, references=(), iloc_version=1, offset_size=4, length_size=4,
          base_size=0, index_size=0, split_extents=False, idat_items=()):
    """HEIF bytes. `references` are (kind, from_index, [to_indices]); items in `idat_items`
    (by index) are stored in an idat box with construction method 1."""
    for i, item in enumerate(items):
        item.id = i + 1
    infe = b''.join(full_box(b'infe', 2, 1 if it.hidden else 0, struct.pack('>HH', it.id, 0) + it.kind + b'\x00') for it in items)
    iinf = full_box(b'iinf', 0, 0, struct.pack('>H', len(items)) + infe)
    props, assoc = [], []
    for it in items:
        indices = []
        for p, essential in it.properties:
            props.append(p)
            indices.append((len(props), essential))
        assoc.append(indices)
    ipco = box(b'ipco', b''.join(props))
    ipma_body = struct.pack('>I', len(items))
    for it, indices in zip(items, assoc):
        ipma_body += struct.pack('>HB', it.id, len(indices)) + b''.join(bytes([(0x80 if e else 0) | n]) for n, e in indices)
    iprp = box(b'iprp', ipco + full_box(b'ipma', 0, 0, ipma_body))
    iref = b''
    if references:
        body = b''
        for kind, source, targets in references:
            body += box(kind, struct.pack('>HH', items[source].id, len(targets)) + b''.join(struct.pack('>H', items[t].id) for t in targets))
        iref = full_box(b'iref', 0, 0, body)
    idat_data = b''.join(items[i].data for i in idat_items)
    idat = box(b'idat', idat_data) if idat_items else b''
    hdlr = full_box(b'hdlr', 0, 0, b'\x00' * 4 + b'pict' + b'\x00' * 12 + b'\x00')
    dinf = box(b'dinf', full_box(b'dref', 0, 0, struct.pack('>I', 1) + full_box(b'url ', 0, 1, b'')))
    pitm = full_box(b'pitm', 0, 0, struct.pack('>H', items[primary].id))
    ftyp = box(b'ftyp', b'heic' + b'\x00' * 4 + b'mif1heic')

    def iloc(mdat_start):
        field = {0: '', 4: 'I', 8: 'Q'}
        body = bytes([(offset_size << 4) | length_size, (base_size << 4) | (index_size if iloc_version else 0)])
        body += struct.pack('>H' if iloc_version < 2 else '>I', len(items))
        position = mdat_start
        idat_position = 0
        for i, it in enumerate(items):
            in_idat = i in idat_items
            method = 1 if in_idat else 0
            body += struct.pack('>H' if iloc_version < 2 else '>I', it.id)
            if iloc_version:
                body += struct.pack('>H', method)
            body += struct.pack('>H', 0)
            start = idat_position if in_idat else position
            base = start if base_size else 0
            if base_size:
                body += struct.pack('>' + field[base_size], base)
            pieces = [it.data] if not split_extents or len(it.data) < 8 else [it.data[:len(it.data) // 3], it.data[len(it.data) // 3:]]
            body += struct.pack('>H', len(pieces))
            offset = start - base
            for piece in pieces:
                if iloc_version and index_size:
                    body += struct.pack('>' + field[index_size], 0)
                if offset_size:
                    body += struct.pack('>' + field[offset_size], offset)
                body += struct.pack('>' + field[length_size], len(piece))
                offset += len(piece)
            if in_idat:
                idat_position += len(it.data)
            else:
                position += len(it.data)
        return full_box(b'iloc', iloc_version, 0, body)

    def assemble(mdat_start):
        meta = full_box(b'meta', 0, 0, hdlr + dinf + pitm + iinf + iref + iprp + idat + iloc(mdat_start))
        return ftyp + meta
    head = assemble(0)
    mdat_start = len(head) + 8
    head = assemble(mdat_start)
    assert len(head) + 8 == mdat_start
    mdat = box(b'mdat', b''.join(it.data for i, it in enumerate(items) if i not in idat_items))
    return head + mdat


def grid_descriptor(rows, columns, width, height):
    return bytes([0, 0, rows - 1, columns - 1]) + struct.pack('>HH', width, height)

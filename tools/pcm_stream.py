"""A tiny HEVC encoder for PCM coverage: no available encoder writes PCM blocks.

It writes one intra picture of 16x16 CTBs where each coding unit is either PCM (raw
samples at a reduced bit depth) or a prediction-only intra block with no residual, so a
decoder must get pcm_flag, PCM alignment and CABAC restarts, PCM sample scaling and the
PCM loop-filter rule right next to ordinary intra prediction and deblocking. The CABAC
encoder follows the HEVC reference encoder. Python standard library only.
"""
import random

# initValue (initType 0) of the contexts used, and the CABAC tables.
INIT = {'split_cu': [139, 141, 157], 'part_mode': [184], 'prev_intra': [184], 'chroma_mode': [63], 'cbf_luma': [111, 141], 'cbf_chroma': [94, 138, 182, 154]}
RANGE_LPS = [
    [128, 176, 208, 240], [128, 167, 197, 227], [128, 158, 187, 216], [123, 150, 178, 205], [116, 142, 169, 195], [111, 135, 160, 185],
    [105, 128, 152, 175], [100, 122, 144, 166], [95, 116, 137, 158], [90, 110, 130, 150], [85, 104, 123, 142], [81, 99, 117, 135],
    [77, 94, 111, 128], [73, 89, 105, 122], [69, 85, 100, 116], [66, 80, 95, 110], [62, 76, 90, 104], [59, 72, 86, 99],
    [56, 69, 81, 94], [53, 65, 77, 89], [51, 62, 73, 85], [48, 59, 69, 80], [46, 56, 66, 76], [43, 53, 63, 72],
    [41, 50, 59, 69], [39, 48, 56, 65], [37, 45, 54, 62], [35, 43, 51, 59], [33, 41, 48, 56], [32, 39, 46, 53],
    [30, 37, 43, 50], [29, 35, 41, 48], [27, 33, 39, 45], [26, 31, 37, 43], [24, 30, 35, 41], [23, 28, 33, 39],
    [22, 27, 32, 37], [21, 26, 30, 35], [20, 24, 29, 33], [19, 23, 27, 31], [18, 22, 26, 30], [17, 21, 25, 28],
    [16, 20, 23, 27], [15, 19, 22, 25], [14, 18, 21, 24], [14, 17, 20, 23], [13, 16, 19, 22], [12, 15, 18, 21],
    [12, 14, 17, 20], [11, 14, 16, 19], [11, 13, 15, 18], [10, 12, 15, 17], [10, 12, 14, 16], [9, 11, 13, 15],
    [9, 11, 12, 14], [8, 10, 12, 14], [8, 9, 11, 13], [7, 9, 11, 12], [7, 9, 10, 12], [7, 8, 10, 11],
    [6, 8, 9, 11], [6, 7, 9, 10], [6, 7, 8, 9], [2, 2, 2, 2]]
NEXT_LPS = [0, 0, 1, 2, 2, 4, 4, 5, 6, 7, 8, 9, 9, 11, 11, 12, 13, 13, 15, 15, 16, 16, 18, 18, 19, 19, 21, 21, 22, 22, 23, 24,
            24, 25, 26, 26, 27, 27, 28, 29, 29, 30, 30, 30, 31, 32, 32, 33, 33, 33, 34, 34, 35, 35, 35, 36, 36, 36, 37, 37, 37, 38, 38, 63]


class Writer:
    def __init__(self):
        self.bits = []

    def u(self, value, count):
        self.bits += [(value >> (count - 1 - i)) & 1 for i in range(count)]

    def ue(self, value):
        value += 1
        n = value.bit_length()
        self.u(0, n - 1)
        self.u(value, n)

    def se(self, value):
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def align(self, bit=0):
        while len(self.bits) % 8:
            self.bits.append(bit)

    def trailing(self):
        self.bits.append(1)
        self.align()

    def bytes(self):
        assert len(self.bits) % 8 == 0
        return bytes(int(''.join(map(str, self.bits[i:i + 8])), 2) for i in range(0, len(self.bits), 8))


class Cabac:
    """HM's TEncBinCABAC over a Writer."""
    def __init__(self, out, qp):
        self.out, self.states = out, {}
        for name, values in INIT.items():
            for i, v in enumerate(values):
                m, n = (v >> 4) * 5 - 45, ((v & 15) << 3) - 16
                s = min(max(((m * qp) >> 4) + n, 1), 126)
                self.states[(name, i)] = [s - 64, 1] if s > 63 else [63 - s, 0]
        self.start()

    def start(self):
        self.low, self.range, self.left, self.buffered, self.byte = 0, 510, 23, 0, 0xff

    def decision(self, bit, name, index=0):
        state = self.states[(name, index)]
        lps = RANGE_LPS[state[0]][(self.range >> 6) & 3]
        self.range -= lps
        if bit != state[1]:
            shift = 6 if lps < 8 else 5 if lps < 16 else 4 if lps < 32 else 3 if lps < 64 else 2 if lps < 128 else 1
            self.low = (self.low + self.range) << shift
            self.range = lps << shift
            if state[0] == 0:
                state[1] = 1 - state[1]
            state[0] = NEXT_LPS[state[0]]
        else:
            state[0] = min(state[0] + 1, 62)
            if self.range >= 256:
                return
            self.low <<= 1
            self.range <<= 1
            shift = 1
        self.left -= shift
        self.test()

    def bypass(self, bit):
        self.low <<= 1
        if bit:
            self.low += self.range
        self.left -= 1
        self.test()

    def terminate(self, bit):
        self.range -= 2
        if bit:
            self.low += self.range
            self.low <<= 7
            self.range = 2 << 7
            self.left -= 7
        elif self.range >= 256:
            return
        else:
            self.low <<= 1
            self.range <<= 1
            self.left -= 1
        self.test()

    def test(self):
        if self.left < 12:
            lead = self.low >> (24 - self.left)
            self.left += 8
            self.low &= 0xffffffff >> self.left
            if lead == 0xff:
                self.buffered += 1
            elif self.buffered > 0:
                carry = lead >> 8
                self.out.u((self.byte + carry) & 0xff, 8)
                self.byte = lead & 0xff
                for _ in range(self.buffered - 1):
                    self.out.u((0xff + carry) & 0xff, 8)
                self.buffered = 1
            else:
                self.buffered = 1
                self.byte = lead

    def finish(self):
        if self.low >> (32 - self.left):
            self.out.u(self.byte + 1, 8)
            for _ in range(self.buffered - 1):
                self.out.u(0x00, 8)
            self.low -= 1 << (32 - self.left)
        else:
            if self.buffered > 0:
                self.out.u(self.byte, 8)
            for _ in range(self.buffered - 1):
                self.out.u(0xff, 8)
        self.out.u(self.low >> 8, 24 - self.left)


def nal(kind, payload):
    out, zeros = bytearray([kind << 1, 1]), 0
    for b in payload:
        if zeros >= 2 and b <= 3:
            out.append(3)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return b'\x00\x00\x00\x01' + bytes(out)


def ptl(w):
    w.u(0, 2); w.u(0, 1); w.u(1, 5); w.u(0x60000000, 32); w.u(0b1001, 4); w.u(0, 44); w.u(90, 8)


def stream(width, height, pcm_bits=7, pcm_bits_c=6, loop_filter_disabled=True, seed=1):
    rng = random.Random(seed)
    vps = Writer()
    vps.u(0, 4); vps.u(3, 2); vps.u(0, 6); vps.u(0, 3); vps.u(1, 1); vps.u(0xffff, 16); ptl(vps)
    vps.u(1, 1); vps.ue(0); vps.ue(0); vps.ue(0); vps.u(0, 6); vps.ue(0); vps.u(0, 1); vps.u(0, 1); vps.trailing()
    sps = Writer()
    sps.u(0, 4); sps.u(0, 3); sps.u(1, 1); ptl(sps)
    sps.ue(0); sps.ue(1); sps.ue(width); sps.ue(height); sps.u(0, 1); sps.ue(0); sps.ue(0); sps.ue(4)
    sps.u(1, 1); sps.ue(0); sps.ue(0); sps.ue(0)
    sps.ue(0); sps.ue(1); sps.ue(0); sps.ue(2); sps.ue(0); sps.ue(0)
    sps.u(0, 1); sps.u(0, 1); sps.u(0, 1)
    sps.u(1, 1); sps.u(pcm_bits - 1, 4); sps.u(pcm_bits_c - 1, 4); sps.ue(0); sps.ue(1); sps.u(1 if loop_filter_disabled else 0, 1)
    sps.ue(0); sps.u(0, 1); sps.u(0, 1); sps.u(0, 1); sps.u(0, 1); sps.u(0, 1); sps.trailing()
    pps = Writer()
    pps.ue(0); pps.ue(0); pps.u(0, 1); pps.u(0, 1); pps.u(0, 3); pps.u(0, 1); pps.u(0, 1); pps.ue(0); pps.ue(0); pps.se(0)
    pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.se(0); pps.se(0); pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.u(0, 1)
    pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.u(0, 1); pps.ue(0); pps.u(0, 1); pps.u(0, 1); pps.trailing()
    s = Writer()
    s.u(1, 1); s.u(0, 1); s.ue(0); s.ue(2); s.se(0); s.trailing()
    cabac = Cabac(s, 26)
    ctbs_w, ctbs_h = width // 16, height // 16
    depth = {}  # coding-tree depth per 8x8 block, for split_cu_flag contexts

    def pcm_block(size):
        cabac.terminate(1)
        cabac.finish()
        s.u(1, 1)
        s.align()
        for _ in range(size * size):
            s.u(rng.randrange(1 << pcm_bits), pcm_bits)
        for _ in range(2 * (size // 2) ** 2):
            s.u(rng.randrange(1 << pcm_bits_c), pcm_bits_c)
        cabac.start()

    def intra_block():
        cabac.decision(1, 'prev_intra')
        cabac.bypass(0)
        cabac.decision(0, 'chroma_mode')
        cabac.decision(0, 'cbf_chroma', 0)
        cabac.decision(0, 'cbf_chroma', 0)
        cabac.decision(0, 'cbf_luma', 1)

    for cy in range(ctbs_h):
        for cx in range(ctbs_w):
            inc = (cx > 0 and depth[(2 * cx - 1, 2 * cy)] > 0) + (cy > 0 and depth[(2 * cx, 2 * cy - 1)] > 0)
            split = rng.random() < 0.5
            cabac.decision(int(split), 'split_cu', inc)
            for k in range(4):
                depth[(2 * cx + (k & 1), 2 * cy + (k >> 1))] = int(split)
            for block in range(4 if split else 1):
                if split:
                    cabac.decision(1, 'part_mode')
                if rng.random() < 0.6:
                    pcm_block(8 if split else 16)
                else:
                    cabac.terminate(0)
                    intra_block()
            last = cx == ctbs_w - 1 and cy == ctbs_h - 1
            cabac.terminate(1 if last else 0)
    cabac.finish()
    s.trailing()
    return nal(32, vps.bytes()) + nal(33, sps.bytes()) + nal(34, pps.bytes()) + nal(19, s.bytes())

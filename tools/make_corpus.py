#!/usr/bin/env python3
"""Rebuild tests/corpus: HEIC files and their references (macOS only).

The pictures are drawn by `scene`, so the corpus carries no third-party imagery.

Two kinds of files are made:
- sips files: macOS's own HEIC encoder, with sips's decode as a PNG reference (the RGB
  oracle), across sizes, grids, qualities, grayscale, alpha and 10-bit.
- feature files: raw HEVC from kvazaar and x265 exercising tiles, slices, wavefront
  rows, dependent slices, lossless blocks, transform skip, sign hiding, scaling lists,
  QP deltas, monochrome, 10 and 12 bits and deblocking offsets, wrapped by heif_writer
  in varied container layouts. Their reference is a CRC32 of FFmpeg's decode of the same
  stream (bit-exact planes); layouts with rotation, mirroring and cropping also get a
  sips PNG.

Needs sips, ffmpeg, x265 and kvazaar (KVAZAAR=/path/to/kvazaar or on PATH).
"""
import os, shutil, struct, subprocess, sys, tempfile, zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import heif_writer as hw
import pcm_stream

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tests/corpus'
KVAZAAR = os.environ.get('KVAZAAR') or shutil.which('kvazaar')
manifest = []


def scene(width, height, seed=0):
    """Rows of 8-bit RGB for a made-up picture with smooth gradients, ripples, soft
    blobs, hard-edged bars and fine grain, so every prediction mode and filter has work
    and the corpus carries no third-party imagery."""
    import math, random
    rng = random.Random(seed)
    blobs = [(rng.random() * width, rng.random() * height, 8 + rng.random() * width / 4, [rng.randrange(-90, 90) for _ in range(3)]) for _ in range(9)]
    bars = [(rng.randrange(width), rng.randrange(height), rng.randrange(2, 40), rng.randrange(2, 40), [rng.randrange(256) for _ in range(3)]) for _ in range(14)]
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            u, v = x / max(width - 1, 1), y / max(height - 1, 1)
            c = [60 + 150 * u, 40 + 120 * v + 30 * math.sin(9 * u + 4 * v), 170 - 100 * u * v + 25 * math.sin(23 * v)]
            for bx, by, radius, tint in blobs:
                w = math.exp(-((x - bx) ** 2 + (y - by) ** 2) / (radius * radius))
                c = [c[i] + tint[i] * w for i in range(3)]
            for bx, by, bw, bh, color in bars:
                if bx <= x < bx + bw and by <= y < by + bh:
                    c = [0.3 * c[i] + 0.7 * color[i] for i in range(3)]
            row += [max(0, min(255, round(c[i] + rng.gauss(0, 2)))) for i in range(3)]
        rows.append(row)
    return rows


def run(*command, **kw):
    subprocess.run([str(c) for c in command], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def sips_heic(png, heic, options=()):
    run('sips', '-s', 'format', 'heic', *options, png, '--out', heic)


def reference_png(heic, name):
    run('sips', '-s', 'format', 'png', heic, '--out', OUT / name)
    return name


def planes_crc(stream, pixel_format):
    """CRC32 and size of FFmpeg's decode of a raw HEVC stream, planes as the decoder
    serialises them (8-bit bytes or 16-bit little-endian)."""
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'hevc', '-i', str(stream), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', pixel_format, '-'], check=True, capture_output=True).stdout
    return zlib.crc32(raw)


def ffmpeg_yuv(png, yuv, pixel_format, width, height):
    run('ffmpeg', '-v', 'error', '-i', png, '-vf', f'scale={width}:{height}', '-pix_fmt', pixel_format, '-f', 'rawvideo', '-y', yuv)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix='heic-corpus-'))
    # --- sips: Apple's own encoder, RGB references from Apple's decoder.
    for w, h in [(214, 130), (131, 77), (401, 299), (64, 64), (8, 8), (1100, 700)]:
        png = tmp / f'p{w}x{h}.png'
        write_png(png, h, w, 2, 8, scene(w, h, w))
        name = f'sips_{w}x{h}.heic'
        sips_heic(png, OUT / name)
        manifest.append(f'rgb {name} {reference_png(OUT / name, name[:-5] + ".png")} 2')
    png = tmp / 'q.png'
    write_png(png, 300, 400, 2, 8, scene(400, 300, 7))
    for quality in ['10', 'high']:
        name = f'sips_quality_{quality}.heic'
        sips_heic(png, OUT / name, ['-s', 'formatOptions', quality])
        manifest.append(f'rgb {name} {reference_png(OUT / name, name[:-5] + ".png")} 2')
    # Grayscale, alpha and 10-bit sources are written with Python so no extra tools are needed.
    rgb = read_png(png)
    write_png(tmp / 'gray.png', 300, 400, 0, 8, [[round(0.299 * r[3 * x] + 0.587 * r[3 * x + 1] + 0.114 * r[3 * x + 2]) for x in range(400)] for r in rgb])
    sips_heic(tmp / 'gray.png', OUT / 'sips_gray.heic')
    manifest.append(f'rgb sips_gray.heic {reference_png(OUT / "sips_gray.heic", "sips_gray.png")} 2')
    rows = [[v for x in range(400) for v in (r[3 * x], r[3 * x + 1], r[3 * x + 2], x * 255 // 399)] for r in rgb]
    write_png(tmp / 'alpha.png', 300, 400, 6, 8, rows)
    sips_heic(tmp / 'alpha.png', OUT / 'sips_alpha.heic')
    manifest.append(f'rgb sips_alpha.heic {reference_png(OUT / "sips_alpha.heic", "sips_alpha.png")} 2')
    # sips writes gray with alpha incorrectly, so that one is checked against its source.
    grays = read_png(tmp / 'gray.png')
    write_png(OUT / 'sips_gray_alpha.png', 300, 400, 4, 8, [[v for x in range(400) for v in (g[x], 255 - x * 255 // 399)] for g in grays])
    sips_heic(OUT / 'sips_gray_alpha.png', OUT / 'sips_gray_alpha.heic')
    manifest.append('rgb sips_gray_alpha.heic sips_gray_alpha.png 12')
    deep = [[min(65535, v * 200 + (x // 3) * 57 * 255 // 399) for x in range(1200) for v in [r[x]]] for r in rgb]
    write_png(tmp / 'deep.png', 300, 400, 2, 16, deep)
    sips_heic(tmp / 'deep.png', OUT / 'sips_10bit.heic')
    manifest.append(f'rgb sips_10bit.heic {reference_png(OUT / "sips_10bit.heic", "sips_10bit.png")} 1')
    # A smooth synthetic 16-bit picture, so its reference PNG stays under the registry's
    # 1 MiB single-object size; sips grids it.
    write_png(tmp / 'deepgrid.png', 300, 1100, 2, 16, [[v for x in range(1100) for v in (x * 59, y * 218, (((x - 550) ** 2 + (y - 150) ** 2) // 40) % 65536)] for y in range(300)])
    sips_heic(tmp / 'deepgrid.png', OUT / 'sips_10bit_grid.heic')
    manifest.append(f'rgb sips_10bit_grid.heic {reference_png(OUT / "sips_10bit_grid.heic", "sips_10bit_grid.png")} 1')
    # --- Feature streams from kvazaar and x265.
    source = tmp / 'src.png'
    write_png(source, 240, 352, 2, 8, scene(352, 240, 3))
    yuv8 = tmp / 'src8.yuv'
    ffmpeg_yuv(source, yuv8, 'yuv420p', 352, 240)
    gray8 = tmp / 'gray8.yuv'
    ffmpeg_yuv(source, gray8, 'gray', 352, 240)
    yuv10 = tmp / 'src10.yuv'
    ffmpeg_yuv(source, yuv10, 'yuv420p10le', 352, 240)
    yuv12 = tmp / 'src12.yuv'
    ffmpeg_yuv(source, yuv12, 'yuv420p12le', 352, 240)
    roi = tmp / 'roi.txt'
    roi.write_text('4 3\n' + '\n'.join(' '.join(str(((x + y) % 5) * 3 - 6) for x in range(4)) for y in range(3)) + '\n')
    kvazaar_cases = {
        'tiles': ['--tiles', '3x2', '--slices', 'tiles'],
        'tiles_explicit': ['--tiles-width-split', '64,192', '--tiles-height-split', '128'],
        'wpp_dependent': ['--wpp', '--slices', 'wpp'],
        'tiles_wpp': ['--tiles', '2x2', '--wpp'],
        'tiles_wpp_slices': ['--tiles', '2x2', '--wpp', '--slices', 'tiles'],
        'lossless': ['--lossless'],
        'transform_skip': ['--transform-skip', '--signhide', '--full-intra-search'],
        'scaling_default': ['--scaling-list', 'default', '-q', '37'],
        'qp_delta': ['--roi', str(roi), '--set-qp-in-cu'],
        'deblock': ['--deblock', '-5:4', '--sao', 'band'],
        'mono': ['--input-format', 'P400'],
    }
    for name, options in kvazaar_cases.items():
        if not KVAZAAR:
            break
        stream = tmp / f'k_{name}.hevc'
        recon = tmp / f'k_{name}.yuv'
        run(KVAZAAR, '-i', gray8 if name == 'mono' else yuv8, '--input-res', '352x240', '-n', '1', '-p', '1', '-q', '27', *options, '-o', stream, '--debug', recon)
        # kvazaar's own reconstruction is the reference: FFmpeg cannot decode tiles with wavefront rows.
        add_stream(f'kvazaar_{name}', stream, 352, 240, zlib.crc32(recon.read_bytes()[:352 * 240 * (1 if name == 'mono' else 3) // (1 if name == 'mono' else 2)]))
    x265_cases = {
        'sign_hiding_rdoq': (yuv8, 8, ['--signhide', '--rdoq-level', '2', '--rd', '6', '--tskip', '--scaling-list', 'default']),
        'ctu16_tu4': (yuv8, 8, ['--ctu', '16', '--min-cu-size', '8', '--max-tu-size', '4', '--no-sao']),
        'slices_cu_lossless': (yuv8, 8, ['--slices', '3', '--cu-lossless', '--rd', '5', '--tu-intra-depth', '4']),
        'main10': (yuv10, 10, ['--strong-intra-smoothing', '--deblock', '3:-2']),
        'main12': (yuv12, 12, ['--aq-mode', '3', '--qg-size', '8']),
        'ctu64_aq': (yuv8, 8, ['--ctu', '64', '--aq-mode', '2', '--qg-size', '16', '--no-strong-intra-smoothing']),
    }
    for name, (yuv, depth, options) in x265_cases.items():
        stream = tmp / f'x_{name}.hevc'
        run('x265', '--input', yuv, '--input-res', '352x240', '--fps', '1', '--frames', '1', '--input-depth', depth, '--output-depth', depth,
            '--keyint', '1', '--crf', '24', '--log-level', 'error', *options, '-o', stream)
        add_stream(f'x265_{name}', stream, 352, 240, planes_crc(stream, {8: 'yuv420p', 10: 'yuv420p10le', 12: 'yuv420p12le'}[depth]))
    # PCM: no encoder at hand writes it, so pcm_stream does, next to prediction-only blocks.
    for name, options in {'pcm_7bit_unfiltered': {}, 'pcm_8bit_filtered': {'pcm_bits': 8, 'pcm_bits_c': 8, 'loop_filter_disabled': False, 'seed': 5}}.items():
        stream = tmp / f'{name}.hevc'
        stream.write_bytes(pcm_stream.stream(64, 48, **options))
        add_stream(name, stream, 64, 48, planes_crc(stream, 'yuv420p'))
    # --- Container layouts over one stream, with sips as the RGB oracle.
    stream = tmp / 'layout.hevc'
    run('x265', '--input', yuv8, '--input-res', '352x240', '--fps', '1', '--frames', '1', '--keyint', '1', '--crf', '22', '--log-level', 'error', '-o', stream)
    units = hw.nal_units(stream.read_bytes())
    config, data = hw.hvcc(units), hw.sample(units)
    def coded(extra):
        return hw.Item(b'hvc1', data, [(hw.box(b'hvcC', config), True), (hw.ispe(352, 240), False), (hw.pixi(3, 8), False)] + extra)
    layouts = {
        'rotate90': ([coded([(hw.irot(1), True)])], {}),
        'rotate180_mirror': ([coded([(hw.irot(2), True), (hw.imir(0), True)])], {}),
        'rotate270_mirror': ([coded([(hw.irot(3), True), (hw.imir(1), True)])], {}),
        'clap': ([coded([(hw.clap(300, 196, (-2, 1), (4, 1)), True)])], {}),
        'iloc_v0_base8': ([coded([])], {'iloc_version': 0, 'base_size': 8, 'offset_size': 0, 'length_size': 8}),
        'iloc_v2_extents': ([coded([])], {'iloc_version': 2, 'offset_size': 8, 'length_size': 4, 'split_extents': True}),
        'iloc_v1_index': ([coded([])], {'iloc_version': 1, 'index_size': 4}),
        'nclx_709_limited': ([coded([(hw.nclx(1, False), False)])], {}),
    }
    for name, (items, options) in layouts.items():
        path = OUT / f'layout_{name}.heic'
        path.write_bytes(hw.write(items, 0, **options))
        if name.startswith('iloc'):
            manifest.append(f'planes {path.name} {planes_crc(stream, "yuv420p"):08x} 0')
        else:
            # sips leaves rotation and mirroring to the PNG's EXIF orientation; apply it.
            reference = reference_png(path, path.stem + '.png')
            orient_png(OUT / reference)
            # These streams have no color description, so they decode as BT.709 limited range,
            # where macOS scales chroma by 255/256, up to a level off the standard.
            manifest.append(f'rgb {path.name} {reference} 3')
    # A 2x2 grid of one tile stream, with its descriptor in idat, and an alpha grid.
    tile_stream = tmp / 'tile.hevc'
    ffmpeg_yuv(source, tmp / 'tile.yuv', 'yuv420p', 128, 96)
    run('x265', '--input', tmp / 'tile.yuv', '--input-res', '128x96', '--fps', '1', '--frames', '1', '--keyint', '1', '--crf', '22', '--log-level', 'error', '-o', tile_stream)
    tile_units = hw.nal_units(tile_stream.read_bytes())
    # The color description sits on the tiles only: a grid without colr takes its tiles'.
    tile = lambda: hw.Item(b'hvc1', hw.sample(tile_units), [(hw.box(b'hvcC', hw.hvcc(tile_units)), True), (hw.ispe(128, 96), False), (hw.pixi(3, 8), False), (hw.nclx(6, True), False)], hidden=True)
    grid = hw.Item(b'grid', hw.grid_descriptor(2, 2, 250, 180), [(hw.ispe(250, 180), False), (hw.pixi(3, 8), False)])
    items = [grid] + [tile() for _ in range(4)]
    path = OUT / 'layout_grid_idat.heic'
    path.write_bytes(hw.write(items, 0, references=[(b'dimg', 0, [1, 2, 3, 4])], idat_items=(0,)))
    manifest.append(f'rgb {path.name} {reference_png(path, path.stem + ".png")} 2')
    (OUT / 'manifest.txt').write_text('# check file reference tolerance: rgb compares with a PNG within `tolerance` 8-bit levels;\n'
                                      '# planes compares a CRC32 of the decoded planes with FFmpeg\'s decode of the stream.\n' + '\n'.join(manifest) + '\n')
    shutil.rmtree(tmp)
    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f'{len(manifest)} corpus entries, {total // 1024} KiB')


def add_stream(name, stream, width, height, crc):
    units = hw.nal_units(stream.read_bytes())
    item = hw.Item(b'hvc1', hw.sample(units), [(hw.box(b'hvcC', hw.hvcc(units)), True), (hw.ispe(width, height), False)])
    (OUT / f'{name}.heic').write_bytes(hw.write([item], 0))
    manifest.append(f'planes {name}.heic {crc:08x} 0')


def orient_png(path):
    """Rewrites an 8-bit RGB PNG with its eXIf orientation applied (and removed)."""
    data = path.read_bytes()
    pos, orientation = 8, 1
    while pos < len(data):
        n, kind = struct.unpack('>I4s', data[pos:pos + 8])
        if kind == b'eXIf':
            exif = data[pos + 8:pos + 8 + n]
            big = exif[:2] == b'MM'
            u16 = lambda at: struct.unpack('>H' if big else '<H', exif[at:at + 2])[0]
            first = struct.unpack('>I' if big else '<I', exif[4:8])[0]
            for i in range(u16(first)):
                entry = first + 2 + 12 * i
                if u16(entry) == 0x0112:
                    orientation = u16(entry + 8)
        pos += 12 + n
    rows = read_png(path)
    height, width = len(rows), len(rows[0]) // 3
    pixel = lambda x, y: rows[y][3 * x:3 * x + 3]
    # EXIF orientation: where each displayed pixel (x, y) comes from in the stored image.
    source = {1: lambda x, y: (x, y), 2: lambda x, y: (width - 1 - x, y), 3: lambda x, y: (width - 1 - x, height - 1 - y),
              4: lambda x, y: (x, height - 1 - y), 5: lambda x, y: (y, x), 6: lambda x, y: (y, height - 1 - x),
              7: lambda x, y: (width - 1 - y, height - 1 - x), 8: lambda x, y: (width - 1 - y, x)}[orientation]
    turned = orientation >= 5
    out_w, out_h = (height, width) if turned else (width, height)
    write_png(path, out_h, out_w, 2, 8, [[v for x in range(out_w) for v in pixel(*source(x, y))] for y in range(out_h)])


def read_png(path):
    """Rows of 8-bit samples of a non-interlaced 8-bit PNG."""
    data = Path(path).read_bytes()
    pos, idat = 8, b''
    while pos < len(data):
        n, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b'IHDR':
            width, height, depth, color = struct.unpack('>IIBB', body[:10])
        elif kind == b'IDAT':
            idat += body
        pos += 12 + n
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    stride = width * channels
    raw = zlib.decompress(idat)
    rows, previous = [], [0] * stride
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = list(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b, c = previous[i], previous[i - channels] if i >= channels else 0
            p = a + b - c
            predictor = [0, a, b, (a + b) // 2, a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c][kind]
            line[i] = (line[i] + predictor) & 255
        rows.append(line)
        previous = line
    if channels == 4:
        rows = [[v for x in range(width) for v in r[4 * x:4 * x + 3]] for r in rows]
    return rows


def write_png(path, height, width, color, depth, rows):
    fmt = '>H' if depth == 16 else '>B'
    raw = b''.join(b'\x00' + b''.join(struct.pack(fmt, v) for v in row) for row in rows)
    chunk = lambda kind, body: struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    main()

# luce-heic

A HEIC/HEIF still-image decoder for Luce/Base: the HEIF container, grid images, alpha, and an intra HEVC decoder. It is pure Luce Base with no platform codec, so iPhone photos open the same way on macOS, Windows and Linux. It depends on luce-raster and luce-std.

```
import heic
from raster import Raster

var image = Raster()                  # image.encoded holds the file's bytes
try heic.probe(&image)                # size, channels, sample type, "HEIC", ICC profile
try image.allocate()
try heic.decode(&image, threads = 4)  # RGB(A), or Y(A) for monochrome
```

`heic.is_heic(data)` recognises the file by its `ftyp` box (bytes 4-7 are `ftyp`, and a
major or compatible brand is heic, heix, heim, heis, hevc, hevx, mif1 or msf1).
`heic.decode_planes(data, &planes)` returns the YCbCr planes before color conversion.

## What it decodes

- **Container:** ftyp, meta, hdlr, pitm, iinf/infe, iloc (versions 0-2, every field size,
  several extents, idat), iprp/ipco/ipma, iref (dimg, auxl), hvcC, ispe, colr (nclx and
  ICC), clap, irot, imir, pixi and auxC. The primary item may be a coded `hvc1` image or a
  `grid` of them; grid tiles decode in parallel. An auxiliary alpha image (itself coded or
  a grid) becomes the alpha channel. Thumbnails and Exif are ignored.
- **HEVC:** I slices of the Main, Main 10, Main Still Picture and (format range) 4:0:0 and
  12-bit profiles; 4:2:0 and 4:0:0; 8 to 12 bits. VPS/SPS/PPS with VUI, slice segments
  and dependent slice segments, tiles, wavefront rows, CABAC with every context, coding
  and transform quadtrees, the 35 intra modes with reference substitution, filtering and
  strong smoothing, the DST and 4-32 point DCT, transform skip, lossless (transquant
  bypass) blocks, PCM, scaling lists, cu_qp_delta, sign data hiding, deblocking and SAO.
- **Output:** uint8 samples for 8-bit images and uint16 (scaled to 0-65535) for deeper
  ones. The matrix and range come from the nclx box, else the stream's VUI, else BT.709
  limited range as HEVC and macOS assume. The ICC profile is kept as the raster attribute
  `icc_profile`. Chroma is upsampled the way macOS ImageIO does, so results match it.
- **Errors, not traps:** corrupt or unsupported input fails with the luce-raster error
  codes.

Not supported: P and B slices (image sequences, bursts and Live Photo video), 4:2:2 and
4:4:4 chroma (sips writes 4:2:2 at its best quality), range-extension tools, multi-layer
streams, AVIF and other codecs, overlay (`iovl`) and identity (`iden`) derived images, and
chroma bit depths that differ from luma.

## Tests

```
./test.sh              # test blocks in native and C modes, then the corpus oracle both ways
```

`tests/oracle.lucb` checks every file in `tests/corpus/manifest.txt`: HEIC files from macOS
`sips` against sips's own decode (RGB within 1-3 levels), and streams from kvazaar, x265
and a small PCM encoder (`tools/pcm_stream.py`) against FFmpeg's or the encoder's own
reconstruction (bit-exact YCbCr planes). Each file also decodes again on worker threads to
the same samples, then 60 truncated and mutated copies of it must fail without a trap.

`tools/make_corpus.py` rebuilds the corpus on macOS (sips, ffmpeg, x265, kvazaar); its
pictures are drawn procedurally. `tests/fuzz.lucb` runs a longer malformed-input campaign
(`fuzz seed variants file.heic...`). `tests/bench.lucb` times a decode: a 12 MP
iPhone-sized grid (3.6 MB) decodes to planes in 1.06 s and to RGB samples in 1.35 s on one
Apple M4 Max core, and to RGB samples in 0.40 s on 4 threads and 0.16 s on 12.

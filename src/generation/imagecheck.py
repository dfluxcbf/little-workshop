"""Telling a ComfyUI picture that came out flat grey from a real one (docs/pillars/generation.md, "Bad outputs").

The fault this guards against (ComfyUI on this machine, 2026-10-22): after the first graph a process runs, the
sampler produces NaN and the picture is one flat grey, 512x512 in about 3.3 KB. Two signals, both measured:

- **small**: a uniform picture compresses to almost nothing, so its compressed pixel data against its raw size is
  a fraction of a percent; a real picture, even a flat icon on a plain background, is several percent.
- **grey**: the pixels barely vary. Decoding costs about a second for a large picture, so only a picture that is
  already small (a suspect) is decoded.

A picture is bad when it is not a PNG this module can read, or when it is both small and grey. Standard library
only: the pillar adds no imaging dependency for one check.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # colour type -> samples per pixel (8-bit, no palette)
SAMPLE_STEP = 4  # every fourth pixel of every fourth row is enough to measure a spread


@dataclass(frozen=True)
class Verdict:
    bad: bool
    reason: str
    width: int = 0
    height: int = 0
    bytes: int = 0
    ratio: float | None = None  # compressed pixel data / raw pixel data
    stddev: float | None = None  # spread of the luminance, 0-255

    def to_dict(self) -> dict:
        return {"bad": self.bad, "reason": self.reason, "width": self.width, "height": self.height, "bytes": self.bytes,
                "ratio": None if self.ratio is None else round(self.ratio, 5),
                "stddev": None if self.stddev is None else round(self.stddev, 2)}


class PngError(ValueError):
    pass


def _chunks(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG")
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise PngError("truncated chunk")
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            return
    raise PngError("no IEND chunk")


def _header(data: bytes) -> tuple[int, int, int, int, bytes]:
    """(width, height, samples per pixel, interlace, compressed pixel data)."""
    width = height = channels = interlace = 0
    idat = bytearray()
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour not in CHANNELS:
                raise PngError(f"unsupported PNG (bit depth {depth}, colour type {colour})")
            channels = CHANNELS[colour]
        elif kind == b"IDAT":
            idat += body
    if not width or not idat:
        raise PngError("no IHDR or IDAT chunk")
    return width, height, channels, interlace, bytes(idat)


def _rows(raw: bytes, width: int, height: int, bpp: int):
    """The unfiltered rows of a non-interlaced 8-bit PNG."""
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        raise PngError("pixel data shorter than the picture")
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif kind == 2:
            row = bytearray((a + b) & 0xFF for a, b in zip(row, previous))
        elif kind == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif kind != 0:
            raise PngError(f"unknown row filter {kind}")
        yield y, row
        previous = row


def luminance_stddev(data: bytes) -> float:
    """The spread of a sample of the picture's luminance (0-255)."""
    width, height, channels, interlace, idat = _header(data)
    if interlace:
        raise PngError("interlaced PNG")
    raw = zlib.decompress(idat)
    values: list[float] = []
    for y, row in _rows(raw, width, height, channels):
        if y % SAMPLE_STEP:
            continue
        for x in range(0, width, SAMPLE_STEP):
            px = row[x * channels:(x + 1) * channels]
            values.append(px[0] if channels < 3 else 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2])
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def check(data: bytes, *, suspect_ratio: float = 0.05, max_stddev: float = 4.0) -> Verdict:
    """Whether a picture is a bad output: unreadable, or small (compressed below `suspect_ratio` of its raw
    size) and grey (luminance spread below `max_stddev`)."""
    try:
        width, height, channels, _, idat = _header(data)
    except PngError as exc:
        return Verdict(True, f"unreadable: {exc}", bytes=len(data))
    ratio = len(idat) / max(1, width * height * channels)
    base = {"width": width, "height": height, "bytes": len(data), "ratio": ratio}
    if ratio >= suspect_ratio:
        return Verdict(False, "ok", **base)
    try:
        spread = luminance_stddev(data)
    except (PngError, zlib.error) as exc:
        return Verdict(True, f"unreadable: {exc}", **base)
    if spread < max_stddev:
        return Verdict(True, f"grey: {width}x{height} compressed to {ratio:.2%} of its raw size, luminance spread {spread:.1f}",
                       stddev=spread, **base)
    return Verdict(False, "ok", stddev=spread, **base)

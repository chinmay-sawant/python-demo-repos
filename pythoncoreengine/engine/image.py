"""Raster image support: JPEG header parsing and PNG decoding (stdlib only).

JPEG is never decoded: the DCT bytes are copied verbatim into a
``/Filter /DCTDecode`` XObject and only the SOF0/SOF1/SOF2 marker is parsed
for width, height and component count.

PNG is fully decoded (zlib + per-scanline unfiltering) into raw pixels,
then re-encoded as a ``/Filter /FlateDecode`` XObject.  Supported: bit depth
8, color types 0 (gray), 2 (RGB) and 6 (RGBA, alpha dropped), filters
0-4 (None/Sub/Up/Average/Paeth), no interlace.

No image is ever rendered to a raster here; only header/dimension work plus
byte transport happens in this module.
"""

from __future__ import annotations

import binascii
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

from .write import N, PdfName

__all__ = [
    "ImageInfo",
    "JPEGImage",
    "PNGImage",
    "decode_png",
    "parse_image",
    "parse_jpeg",
]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SOF_MARKERS = (0xC0, 0xC1, 0xC2)  # baseline, extended sequential, progressive
_MARKERS_WITHOUT_LENGTH = (0x01, 0xD8, 0xD9)  # TEM, SOI, EOI

_COLORSPACE_BY_COMPONENTS = {1: "DeviceGray", 3: "DeviceRGB"}
_CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 6: 4}


class ImageInfo:
    """Shared XObject metadata for a decoded image."""

    kind = "image"

    def __init__(self, *, width: int, height: int, colorspace: str) -> None:
        self.width = width
        self.height = height
        self.colorspace = colorspace

    def xobject_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        """Return ``(stream bytes, extra stream dict entries)`` for the XObject."""
        raise NotImplementedError

    def _image_dict(self) -> Dict[PdfName, Any]:
        return {
            N("Type"): N("XObject"),
            N("Subtype"): N("Image"),
            N("Width"): self.width,
            N("Height"): self.height,
            N("ColorSpace"): N(self.colorspace),
            N("BitsPerComponent"): 8,
        }


class JPEGImage(ImageInfo):
    """A JPEG image; the compressed bytes are stored as-is."""

    kind = "jpeg"

    def __init__(self, data: bytes, width: int, height: int, components: int) -> None:
        try:
            colorspace = _COLORSPACE_BY_COMPONENTS[components]
        except KeyError:
            raise ValueError(
                f"unsupported JPEG component count {components} (need 1 or 3)"
            ) from None
        super().__init__(width=width, height=height, colorspace=colorspace)
        self.data = data
        self.components = components

    def xobject_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        extra = self._image_dict()
        extra[N("Filter")] = N("DCTDecode")
        return self.data, extra


class PNGImage(ImageInfo):
    """A decoded PNG re-encoded as a FlateDecode image XObject.

    ``scanlines`` holds one row of ``width * 3`` RGB bytes per line
    (alpha, if present in the source, has been dropped).
    """

    kind = "png"

    def __init__(
        self,
        *,
        width: int,
        height: int,
        colorspace: str,
        scanlines: bytes,
    ) -> None:
        super().__init__(width=width, height=height, colorspace=colorspace)
        self.scanlines = scanlines

    def pixel_at(self, x: int, y: int) -> Tuple[int, ...]:
        """Return the pixel at ``(x, y)``: 3 RGB values or 1 gray value."""
        channels = 3 if self.colorspace == "DeviceRGB" else 1
        stride = self.width * channels
        start = y * stride + x * channels
        return tuple(self.scanlines[start:start + channels])

    def xobject_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        extra = self._image_dict()
        extra[N("Filter")] = N("FlateDecode")
        return zlib.compress(self.scanlines), extra


def parse_image(data: bytes) -> ImageInfo:
    """Dispatch on the magic bytes: JPEG or PNG, else ``ValueError``."""
    if data.startswith(b"\xff\xd8"):
        return parse_jpeg(data)
    if data.startswith(_PNG_SIGNATURE):
        return decode_png(data)
    raise ValueError("unrecognised image format (expected JPEG or PNG)")


def parse_jpeg(data: bytes) -> JPEGImage:
    """Parse a JPEG stream and return an :class:`JPEGImage`.

    Walks the marker segments from SOI until the first SOF0/SOF1/SOF2
    marker, reading precision/height/width/components from its payload.
    The compressed bytes are retained verbatim.
    """
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG stream (missing SOI marker)")
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError("malformed JPEG: expected marker byte")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in _MARKERS_WITHOUT_LENGTH or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            raise ValueError("malformed JPEG: truncated segment length")
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        payload = data[pos + 2:pos + length]
        if marker in _SOF_MARKERS:
            if len(payload) < 6:
                raise ValueError("malformed JPEG: short SOF payload")
            height = struct.unpack(">H", payload[1:3])[0]
            width = struct.unpack(">H", payload[3:5])[0]
            return JPEGImage(data, width, height, payload[5])
        pos += length
    raise ValueError("JPEG SOF0/SOF1/SOF2 marker not found")


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk (length, kind, payload, CRC) — used by tests and fixtures."""
    length = struct.pack(">I", len(payload))
    crc = binascii.crc32(payload, binascii.crc32(kind))
    return length + kind + payload + struct.pack(">I", crc & 0xFFFFFFFF)


def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    """The PNG Paeth predictor over three neighbouring bytes."""
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_up_left = abs(estimate - up_left)
    if dist_left <= dist_up and dist_left <= dist_up_left:
        return left
    if dist_up <= dist_up_left:
        return up
    return up_left


def decode_png(data: bytes) -> PNGImage:
    """Decode an 8-bit non-interlaced PNG into raw pixel scanlines.

    Filters 0-4 (None/Sub/Up/Average/Paeth) are undone per scanline with
    the PNG specification's reconstruction formulas; RGBA sources drop the
    alpha byte per pixel, gray sources stay single-channel.
    """
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG stream")
    pos = len(_PNG_SIGNATURE)
    header: Optional[Tuple[int, int, int]] = None
    idat: List[bytes] = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            if len(payload) < 13:
                raise ValueError("malformed PNG: short IHDR")
            width = struct.unpack(">I", payload[0:4])[0]
            height = struct.unpack(">I", payload[4:8])[0]
            bit_depth = payload[8]
            color_type = payload[9]
            if bit_depth != 8:
                raise ValueError(f"unsupported PNG bit depth {bit_depth} (only 8)")
            if payload[10] != 0 or payload[11] != 0 or payload[12] != 0:
                raise ValueError("unsupported PNG compression, filter or interlace")
            header = (width, height, color_type)
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None:
        raise ValueError("PNG missing IHDR chunk")
    if not idat:
        raise ValueError("PNG missing IDAT chunk")
    width, height, color_type = header
    try:
        channels = _CHANNELS_BY_COLOR_TYPE[color_type]
    except KeyError:
        raise ValueError(
            f"unsupported PNG color type {color_type} (need 0, 2 or 6)"
        ) from None

    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    if len(raw) != (stride + 1) * height:
        raise ValueError(
            f"PNG scanline data mismatch: expected {(stride + 1) * height} bytes, "
            f"got {len(raw)}"
        )

    out = bytearray()
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        if filter_type > 4:
            raise ValueError(f"invalid PNG filter type {filter_type}")
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        for index in range(stride):
            value = line[index]
            left = line[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                value += left
            elif filter_type == 2:
                value += up
            elif filter_type == 3:
                value += (left + up) // 2
            elif filter_type == 4:
                value += _paeth_predictor(left, up, up_left)
            line[index] = value & 0xFF
        if color_type == 6:
            for index in range(0, stride, 4):
                out.extend(line[index:index + 3])
        else:
            out.extend(line)
        previous = line

    colorspace = "DeviceGray" if color_type == 0 else "DeviceRGB"
    return PNGImage(width=width, height=height, colorspace=colorspace, scanlines=bytes(out))

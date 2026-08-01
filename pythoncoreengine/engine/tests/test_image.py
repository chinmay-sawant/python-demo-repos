"""Unit tests for engine/image.py: JPEG header parse, PNG decode, XObjects.

A synthetic PNG is built in-repo (zlib + correct chunk framing and filters)
so decoding can be verified pixel-by-pixel against known data, including
every filter type 0-4.  JPEG coverage uses synthetic SOF0/SOF1/SOF2 headers.
"""

from __future__ import annotations

import struct
import unittest
import zlib

from engine import DocumentBuilder, decode_png, parse_image, parse_jpeg
from engine.image import PNGImage, _png_chunk
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
    refs_in,
)

# ---------------------------------------------------------------------
# Synthetic image builders (deterministic, stdlib only)
# ---------------------------------------------------------------------


def make_png(width: int, height: int, color_type: int, filter_type: int, pixels: bytes) -> bytes:
    """Build a PNG whose scanlines all use ``filter_type`` (0-4)."""
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(filter_type)
        raw.extend(pixels[y * stride:(y + 1) * stride])
    ihdr = (
        struct.pack(">II", width, height)
        + bytes([8, color_type, 0, 0, 0])
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _png_chunk(b"IEND", b"")
    )


def make_gradient_png(width: int = 8, height: int = 8, filter_type: int = 0) -> bytes:
    """A deterministic RGB gradient: pixel (x, y) = (x*31, y*31, (x+y)*31)."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend([x * 31, y * 31, ((x + y) * 31) & 0xFF])
    return make_png(width, height, 2, filter_type, bytes(pixels))


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_up_left = abs(estimate - up_left)
    if dist_left <= dist_up and dist_left <= dist_up_left:
        return left
    if dist_up <= dist_up_left:
        return up
    return up_left


def _encode_scanline(pixels: bytes, filter_type: int, channels: int, prev: bytes) -> bytes:
    """Apply a PNG filter to raw pixel bytes (the inverse of decoding)."""
    out = bytearray()
    for index, value in enumerate(pixels):
        left = pixels[index - channels] if index >= channels else 0
        up = prev[index]
        up_left = prev[index - channels] if index >= channels else 0
        if filter_type == 0:
            filtered = value
        elif filter_type == 1:
            filtered = value - left
        elif filter_type == 2:
            filtered = value - up
        elif filter_type == 3:
            filtered = value - (left + up) // 2
        else:
            filtered = value - _paeth(left, up, up_left)
        out.append(filtered & 0xFF)
    return bytes(out)


def make_filtered_png(
    width: int, height: int, color_type: int, rows: list, filters: list
) -> bytes:
    """Build a PNG from raw pixel rows with explicit per-row filter types."""
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    stride = width * channels
    raw = bytearray()
    prev = b"\x00" * stride
    for y in range(height):
        row = bytes(rows[y])
        raw.append(filters[y])
        raw.extend(_encode_scanline(row, filters[y], channels, prev))
        prev = row
    ihdr = struct.pack(">II", width, height) + bytes([8, color_type, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _png_chunk(b"IEND", b"")
    )


def make_jpeg(width: int = 16, height: int = 16, components: int = 3, marker: int = 0xC0) -> bytes:
    """A structurally valid JPEG: SOI + SOF + SOS + dummy entropy + EOI."""
    sof = bytes([8]) + struct.pack(">HH", height, width) + bytes([components])
    sos = bytes([8, 2]) + bytes([1, 1, 0, 0, 63, 0])
    return (
        b"\xff\xd8"
        + bytes([0xFF, marker])
        + struct.pack(">H", len(sof) + 2)
        + sof
        + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
        + b"\x00\x01"
        + b"\xff\xd9"
    )


# ---------------------------------------------------------------------
# JPEG header parsing
# ---------------------------------------------------------------------


class TestJPEGParse(unittest.TestCase):
    def test_sof0_baseline(self) -> None:
        info = parse_jpeg(make_jpeg(16, 16, 3, 0xC0))
        self.assertEqual((info.width, info.height), (16, 16))
        self.assertEqual(info.colorspace, "DeviceRGB")
        self.assertEqual(info.kind, "jpeg")

    def test_sof1_extended_sequential(self) -> None:
        info = parse_jpeg(make_jpeg(32, 8, 3, 0xC1))
        self.assertEqual((info.width, info.height), (32, 8))

    def test_sof2_progressive(self) -> None:
        info = parse_jpeg(make_jpeg(10, 20, 3, 0xC2))
        self.assertEqual((info.width, info.height), (10, 20))

    def test_gray_components(self) -> None:
        info = parse_jpeg(make_jpeg(4, 4, 1))
        self.assertEqual(info.colorspace, "DeviceGray")

    def test_unsupported_components_raise(self) -> None:
        with self.assertRaises(ValueError):
            parse_jpeg(make_jpeg(4, 4, 4))

    def test_missing_soi_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_jpeg(b"\x00\x00\x00")

    def test_missing_sof_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_jpeg(b"\xff\xd8\xff\xd9")

    def test_original_bytes_kept_verbatim(self) -> None:
        raw = make_jpeg(16, 16)
        info = parse_jpeg(raw)
        self.assertEqual(info.data, raw)


# ---------------------------------------------------------------------
# PNG decoding
# ---------------------------------------------------------------------


class TestPNGDecode(unittest.TestCase):
    def test_rgb_pixels_decode_correctly(self) -> None:
        png = make_gradient_png(8, 8, filter_type=0)
        image = decode_png(png)
        self.assertEqual((image.width, image.height), (8, 8))
        self.assertEqual(image.colorspace, "DeviceRGB")
        self.assertEqual(image.pixel_at(3, 5), (93, 155, 248))

    def test_rgba_drops_alpha(self) -> None:
        pixels = bytearray()
        for y in range(2):
            for x in range(2):
                pixels.extend([x * 50, y * 100, 7, 255])
        image = decode_png(make_png(2, 2, 6, 0, bytes(pixels)))
        self.assertEqual(image.colorspace, "DeviceRGB")
        self.assertEqual(image.pixel_at(1, 0), (50, 0, 7))
        self.assertEqual(image.width * image.height * 3, len(image.scanlines))

    def test_gray_stays_single_channel(self) -> None:
        pixels = bytes(range(9))
        image = decode_png(make_png(3, 3, 0, 0, pixels))
        self.assertEqual(image.colorspace, "DeviceGray")
        self.assertEqual(image.pixel_at(2, 2), (8,))

    def test_filter_none(self) -> None:
        image = decode_png(make_gradient_png(4, 4, filter_type=0))
        self.assertEqual(image.pixel_at(1, 1), (31, 31, 62))

    def test_filter_sub(self) -> None:
        # Constant rows: Sub-encoded deltas must reconstruct to the constant.
        rows = [bytes([9] * 12), bytes([7] * 12)]
        image = decode_png(make_filtered_png(4, 2, 2, rows, [1, 1]))
        self.assertEqual(image.pixel_at(3, 0), (9, 9, 9))
        self.assertEqual(image.pixel_at(0, 1), (7, 7, 7))

    def test_filter_up(self) -> None:
        rows = [
            bytes([200, 100, 50] * 4),
            bytes([10, 20, 30] * 4),
        ]
        image = decode_png(make_filtered_png(4, 2, 2, rows, [2, 2]))
        self.assertEqual(image.pixel_at(1, 1), (10, 20, 30))

    def test_filter_average(self) -> None:
        rows = [
            bytes([10, 20, 30] * 4),
            bytes([40, 50, 60] * 4),
        ]
        image = decode_png(make_filtered_png(4, 2, 2, rows, [3, 3]))
        self.assertEqual(image.pixel_at(2, 1), (40, 50, 60))

    def test_filter_paeth(self) -> None:
        rows = [bytes([123] * 12), bytes([123] * 12)]
        image = decode_png(make_filtered_png(4, 2, 2, rows, [4, 4]))
        self.assertEqual(image.pixel_at(0, 0), (123, 123, 123))
        self.assertEqual(image.pixel_at(3, 1), (123, 123, 123))

    def test_mixed_filters_round_trip(self) -> None:
        # One scanline per filter type (0-4), distinct values per row.
        rows = [bytes([value * 11] * 12) for value in range(5)]
        image = decode_png(make_filtered_png(4, 5, 2, rows, list(range(5))))
        for row, value in enumerate(range(5)):
            self.assertEqual(
                image.pixel_at(4 % 4, row), (value * 11, value * 11, value * 11)
            )

    def test_truncated_data_raises(self) -> None:
        png = make_gradient_png(4, 4)
        with self.assertRaises(Exception):
            decode_png(png[: len(png) // 2])

    def test_bad_signature_raises(self) -> None:
        with self.assertRaises(ValueError):
            decode_png(b"not a png at all")

    def test_parse_image_dispatches(self) -> None:
        self.assertIsInstance(parse_image(make_gradient_png()), PNGImage)
        with self.assertRaises(ValueError):
            parse_image(b"garbage")


# ---------------------------------------------------------------------
# XObject emission through DocumentBuilder
# ---------------------------------------------------------------------


def _image_document(first: bytes, second: bytes) -> tuple:
    builder = DocumentBuilder()
    flow = builder.flow()
    flow.image(first, x=20, y=30, width=64, height=64)
    flow.image(second, x=100, y=30, width=64, height=64)
    data = builder.render()
    return data, parse_xref(data)


class TestImageXObject(unittest.TestCase):
    def test_png_xobject_dict_keys(self) -> None:
        data, offsets = _image_document(make_gradient_png(), make_gradient_png())
        image_id = find_object_with(data, b"/Subtype /Image", offsets)
        body = object_bytes(data, offsets[image_id])
        for key in (
            b"/Type /XObject",
            b"/Subtype /Image",
            b"/Width 8",
            b"/Height 8",
            b"/ColorSpace /DeviceRGB",
            b"/BitsPerComponent 8",
            b"/Filter /FlateDecode",
            b"/Length",
        ):
            self.assertIn(key, body)
        stream = inflate_stream(data, offsets[image_id])
        image = PNGImage(width=8, height=8, colorspace="DeviceRGB", scanlines=stream)
        self.assertEqual(image.pixel_at(2, 2), (62, 62, 124))

    def test_jpeg_xobject_dict_keys(self) -> None:
        data, offsets = _image_document(make_jpeg(), make_jpeg())
        image_id = find_object_with(data, b"/Filter /DCTDecode", offsets)
        body = object_bytes(data, offsets[image_id])
        for key in (
            b"/Type /XObject",
            b"/Subtype /Image",
            b"/Width 16",
            b"/Height 16",
            b"/ColorSpace /DeviceRGB",
            b"/Filter /DCTDecode",
        ):
            self.assertIn(key, body)

    def test_same_image_twice_yields_one_xobject(self) -> None:
        data, offsets = _image_document(make_gradient_png(), make_gradient_png())
        images = [
            obj
            for obj, _ in offsets.items()
            if b"/Subtype /Image" in object_bytes(data, offsets[obj])
        ]
        self.assertEqual(len(images), 1)

    def test_different_images_yield_two_xobjects(self) -> None:
        data, offsets = _image_document(
            make_gradient_png(), make_gradient_png(4, 4)
        )
        images = [
            obj
            for obj, _ in offsets.items()
            if b"/Subtype /Image" in object_bytes(data, offsets[obj])
        ]
        self.assertEqual(len(images), 2)

    def test_content_stream_draws_image_with_matrix(self) -> None:
        data, offsets = _image_document(make_gradient_png(), make_gradient_png())
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        page_body = object_bytes(data, offsets[page_id])
        for ref in refs_in(page_body):
            if b"/Filter /FlateDecode" in object_bytes(data, offsets[ref]):
                stream = inflate_stream(data, offsets[ref])
                self.assertIn(b"q", stream)
                self.assertIn(b"64 0 0 64", stream)
                self.assertIn(b"cm", stream)
                self.assertIn(b"/Im1 Do", stream)
                self.assertIn(b"Q", stream)
                return
        self.fail("no compressed content stream found")


if __name__ == "__main__":
    unittest.main()

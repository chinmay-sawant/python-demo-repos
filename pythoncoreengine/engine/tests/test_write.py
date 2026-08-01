"""Unit tests for engine/write.py byte-level encodings (round-trips)."""

from __future__ import annotations

import datetime
import unittest

from engine.write import (
    ByteWriter,
    N,
    ObjectId,
    PdfHexString,
    PdfName,
    encode_array,
    encode_dict,
    encode_hex_string,
    encode_name,
    encode_object,
    encode_stream_object,
    encode_string,
    encode_value,
    encode_xref_section,
    escape_name,
    escape_string,
    format_date,
    format_number,
)

_ESCAPE_DECODE = {
    ord("n"): 0x0A,
    ord("r"): 0x0D,
    ord("t"): 0x09,
    ord("b"): 0x08,
    ord("f"): 0x0C,
}


def unescape_pdf_string(raw: bytes) -> bytes:
    """Inverse of escape_string, for round-trip assertions."""
    out = bytearray()
    i = 0
    while i < len(raw):
        byte = raw[i]
        if byte != 0x5C:
            out.append(byte)
            i += 1
            continue
        i += 1
        if i >= len(raw):
            out.append(0x5C)
            break
        nxt = raw[i]
        if nxt in b"nrtbf":
            out.append(_ESCAPE_DECODE[nxt])
            i += 1
        elif nxt in b"()\\":
            out.append(nxt)
            i += 1
        elif 0x30 <= nxt <= 0x37:
            digits = bytearray()
            while i < len(raw) and len(digits) < 3 and 0x30 <= raw[i] <= 0x37:
                digits.append(raw[i])
                i += 1
            out.append(int(digits, 8))
        else:
            out.append(nxt)
            i += 1
    return bytes(out)


class TestFormatNumber(unittest.TestCase):
    def test_integers(self) -> None:
        self.assertEqual(format_number(0), "0")
        self.assertEqual(format_number(1), "1")
        self.assertEqual(format_number(-3), "-3")

    def test_integral_floats_lose_decimal(self) -> None:
        self.assertEqual(format_number(1.0), "1")
        self.assertEqual(format_number(2.0), "2")

    def test_fractional_floats_keep_needed_digits(self) -> None:
        self.assertEqual(format_number(1.5), "1.5")
        self.assertEqual(format_number(595.276), "595.276")
        self.assertEqual(format_number(841.89), "841.89")
        self.assertEqual(format_number(-0.25), "-0.25")

    def test_no_exponent_form(self) -> None:
        self.assertNotIn("e", format_number(1e20))
        self.assertNotIn("E", format_number(1e-10))

    def test_bool_rejected(self) -> None:
        with self.assertRaises(TypeError):
            format_number(True)  # type: ignore[arg-type]


class TestFormatDate(unittest.TestCase):
    def test_pdf_date_format(self) -> None:
        when = datetime.datetime(2026, 8, 1, 12, 0, 0)
        self.assertEqual(format_date(when), "D:20260801120000")


class TestStringEscaping(unittest.TestCase):
    def test_plain_bytes_unchanged(self) -> None:
        self.assertEqual(escape_string(b"Hello"), b"Hello")

    def test_special_characters_escaped(self) -> None:
        self.assertEqual(escape_string(b"(paren)"), b"\\(paren\\)")
        self.assertEqual(escape_string(b"a\\b"), b"a\\\\b")

    def test_control_and_high_bytes_octal(self) -> None:
        self.assertEqual(escape_string(b"a\nb"), b"a\\012b")
        self.assertEqual(escape_string(b"\x80"), b"\\200")
        self.assertEqual(escape_string(b"\x1b"), b"\\033")

    def test_round_trip(self) -> None:
        samples = [
            b"",
            b"Hello, world",
            b"(a) [b] <c> {d} /e %f",
            b"line1\nline2\ttab\x0c\b\r",
            bytes(range(256)),
        ]
        for sample in samples:
            self.assertEqual(unescape_pdf_string(escape_string(sample)), sample)

    def test_encode_string(self) -> None:
        self.assertEqual(encode_string("Hello"), b"(Hello)")
        self.assertEqual(encode_string(b"a(b)"), b"(a\\(b\\))")


class TestNames(unittest.TestCase):
    def test_escape_regular_names(self) -> None:
        self.assertEqual(escape_name("F1"), b"F1")
        self.assertEqual(escape_name("Helvetica"), b"Helvetica")

    def test_escape_special_characters(self) -> None:
        self.assertEqual(escape_name("a b#c"), b"a#20b#23c")
        self.assertEqual(escape_name("x/y"), b"x#2Fy")

    def test_encode_name(self) -> None:
        self.assertEqual(encode_name("F1"), b"/F1")

    def test_pdfname_normalises_slash(self) -> None:
        self.assertEqual(PdfName("Type"), "/Type")
        self.assertEqual(PdfName("/Type"), "/Type")
        self.assertEqual(N("Catalog"), "/Catalog")
        self.assertEqual(encode_value(N("Catalog")), b"/Catalog")

    def test_name_round_trip(self) -> None:
        raw = escape_name("a b#c")
        out = bytearray()
        i = 0
        while i < len(raw):
            if raw[i] == 0x23 and i + 2 < len(raw):
                out.append(int(raw[i + 1:i + 3], 16))
                i += 3
            else:
                out.append(raw[i])
                i += 1
        self.assertEqual(bytes(out), "a b#c".encode("ascii"))


class TestHexStrings(unittest.TestCase):
    def test_encode_hex_string(self) -> None:
        self.assertEqual(encode_hex_string(b"\xab\xcd"), b"<abcd>")
        self.assertEqual(encode_hex_string(b""), b"<>")

    def test_hex_string_value_dispatch(self) -> None:
        self.assertEqual(encode_value(PdfHexString(b"\xde\xad")), b"<dead>")


class TestObjectId(unittest.TestCase):
    def test_render_ref(self) -> None:
        self.assertEqual(ObjectId(3).render_ref(), b"3 0 R")
        self.assertEqual(encode_value(ObjectId(7)), b"7 0 R")

    def test_zero_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ObjectId(0)


class TestContainers(unittest.TestCase):
    def test_dict_encoding(self) -> None:
        encoded = encode_dict({N("Type"): N("Catalog"), N("Pages"): ObjectId(3)})
        self.assertEqual(encoded, b"<< /Type /Catalog /Pages 3 0 R >>")

    def test_dict_nested(self) -> None:
        encoded = encode_dict(
            {N("Resources"): {N("Font"): {N("F1"): ObjectId(5)}}}
        )
        self.assertEqual(encoded, b"<< /Resources << /Font << /F1 5 0 R >> >> >>")

    def test_dict_empty(self) -> None:
        self.assertEqual(encode_dict({}), b"<<  >>")

    def test_array_encoding(self) -> None:
        encoded = encode_array([0, 0, 595.276, 841.89])
        self.assertEqual(encoded, b"[0 0 595.276 841.89]")

    def test_array_of_references(self) -> None:
        encoded = encode_value([ObjectId(1), ObjectId(2)])
        self.assertEqual(encoded, b"[1 0 R 2 0 R]")

    def test_string_value_is_literal(self) -> None:
        self.assertEqual(encode_value("D:20260801120000"), b"(D:20260801120000)")

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(TypeError):
            encode_value(object())


class TestObjects(unittest.TestCase):
    def test_plain_object(self) -> None:
        encoded = encode_object(1, {N("Type"): N("Catalog")})
        self.assertEqual(encoded, b"1 0 obj\n<< /Type /Catalog >>\nendobj\n")

    def test_stream_object_length(self) -> None:
        data = b"BT\nET\n"
        encoded = encode_stream_object(7, data)
        self.assertTrue(encoded.startswith(b"7 0 obj\n"))
        self.assertIn(b"<< /Length 6 >>\nstream\n", encoded)
        self.assertIn(data + b"\nendstream\nendobj\n", encoded)
        self.assertNotIn(b"/Filter", encoded)

    def test_stream_object_extra_dict_entries(self) -> None:
        encoded = encode_stream_object(2, b"abc", {N("Filter"): N("FlateDecode")})
        self.assertIn(b"<< /Filter /FlateDecode /Length 3 >>", encoded)

    def test_stream_length_overrides_caller(self) -> None:
        encoded = encode_stream_object(2, b"abc", {N("Length"): 999})
        self.assertIn(b"<< /Length 3 >>", encoded)


class TestXrefSection(unittest.TestCase):
    def test_section_layout(self) -> None:
        encoded = encode_xref_section({1: 15, 2: 40}, 3)
        expected = (
            b"xref\n"
            b"0 3\n"
            b"0000000000 65535 f \n"
            b"0000000015 00000 n \n"
            b"0000000040 00000 n \n"
        )
        self.assertEqual(encoded, expected)

    def test_missing_offset_raises(self) -> None:
        with self.assertRaises(ValueError):
            encode_xref_section({1: 15}, 3)


class TestByteWriter(unittest.TestCase):
    def test_tracks_offsets(self) -> None:
        writer = ByteWriter()
        self.assertEqual(writer.tell(), 0)
        writer.write(b"%PDF-2.0\n")
        first = writer.tell()
        self.assertEqual(first, 9)
        writer.write(b"abc")
        self.assertEqual(writer.tell(), 12)
        self.assertEqual(writer.getvalue(), b"%PDF-2.0\nabc")

    def test_write_returns_start_offset(self) -> None:
        writer = ByteWriter()
        start = writer.write(b"xy")
        self.assertEqual(start, 0)
        start = writer.write(b"xy")
        self.assertEqual(start, 2)


if __name__ == "__main__":
    unittest.main()

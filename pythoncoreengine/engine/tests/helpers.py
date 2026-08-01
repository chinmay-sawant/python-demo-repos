"""Tiny hand-rolled PDF syntax parsing helpers for unit tests (stdlib only).

These deliberately avoid any PDF library; they exist to let tests verify
the engine's output byte-for-byte with the smallest possible surface.

Phase 7.4 adds :func:`der_read_element`: a minimal ASN.1 DER reader used
to parse the CMS/PKCS#7 signature back out of a signed file and verify it
in pure Python.
"""

from __future__ import annotations

import re
import zlib
from typing import Any, Dict, List, Optional, Tuple

_XREF_KEYWORD = b"xref\n"
_OBJ_HEADER_RE = re.compile(rb"(\d+)\s+0\s+obj\b")
_SUBSECTION_RE = re.compile(rb"(\d+)\s+(\d+)\s*\n")
_ENTRY_RE = re.compile(rb"(\d{10})\s(\d{5})\s([fn])")
_REF_RE = re.compile(rb"(\d+)\s+0\s+R")
_STREAM_HEADER_RE = re.compile(rb"stream\s*\n")


def parse_xref(data: bytes) -> Dict[int, int]:
    """Parse the first classic xref section; return {obj num: byte offset} for in-use entries."""
    pos = data.index(_XREF_KEYWORD) + len(_XREF_KEYWORD)
    offsets: Dict[int, int] = {}
    while pos < len(data):
        header = _SUBSECTION_RE.match(data, pos)
        if header is None:
            break
        start, count = int(header.group(1)), int(header.group(2))
        pos = header.end()
        for i in range(count):
            entry = _ENTRY_RE.match(data, pos)
            if entry is None:
                raise AssertionError(f"malformed xref entry at offset {pos}")
            offset, _gen, kind = int(entry.group(1)), int(entry.group(2)), entry.group(3)
            if kind == b"n":
                offsets[start + i] = offset
            line_end = data.index(b"\n", pos)
            pos = line_end + 1
    if not offsets:
        raise AssertionError("no in-use xref entries found")
    return offsets


def object_bytes(data: bytes, offset: int) -> bytes:
    """Return the raw bytes of the object starting at ``offset`` (through ``endobj``)."""
    end = data.index(b"endobj", offset)
    return data[offset:end]


def parse_obj_header(data: bytes, offset: int) -> Tuple[int, int]:
    """Parse ``N 0 obj`` at ``offset``; returns (obj num, generation)."""
    match = _OBJ_HEADER_RE.match(data, offset)
    if match is None:
        raise AssertionError(f"no object header at offset {offset}: {data[offset:offset + 24]!r}")
    return int(match.group(1)), 0


def trailer_dict_bytes(data: bytes) -> bytes:
    """Return the trailer dictionary bytes (between ``trailer`` and ``startxref``)."""
    match = re.search(rb"trailer\s*\n(<<.*?>>)\s*\nstartxref", data, re.S)
    if match is None:
        raise AssertionError("no trailer dictionary found")
    return match.group(1)


def startxref_offset(data: bytes) -> int:
    """Return the integer after ``startxref`` (must equal the xref section offset)."""
    match = re.search(rb"startxref\s*\n(\d+)\s*\n%%EOF", data)
    if match is None:
        raise AssertionError("no startxref/%%EOF tail found")
    return int(match.group(1))


def find_object_with(data: bytes, marker: bytes, offsets: Dict[int, int]) -> int:
    """Return the object number of the first in-use object whose body contains ``marker``."""
    for obj_id, offset in sorted(offsets.items()):
        if marker in object_bytes(data, offset):
            return obj_id
    raise AssertionError(f"no object contains {marker!r}")


def trailer_dict_values(trailer: bytes) -> Dict[str, object]:
    """Extract /Size, /Root and /ID from raw trailer dict bytes."""
    size = re.search(rb"/Size\s+(\d+)", trailer)
    root = re.search(rb"/Root\s+(\d+)\s+0\s+R", trailer)
    ids = re.search(rb"/ID\s*\[\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*\]", trailer)
    if size is None or root is None or ids is None:
        raise AssertionError(f"trailer missing /Size, /Root or /ID: {trailer!r}")
    return {
        "size": int(size.group(1)),
        "root": int(root.group(1)),
        "id_first": ids.group(1).decode("ascii"),
        "id_second": ids.group(2).decode("ascii"),
    }


def all_object_ids(offsets: Dict[int, int]) -> List[int]:
    """Return the in-use object numbers in ascending order."""
    return sorted(offsets.keys())


def all_objects_with(data: bytes, offsets: Dict[int, int], marker: bytes) -> List[int]:
    """Object numbers (ascending) whose body contains ``marker``."""
    return [
        obj_id
        for obj_id, offset in sorted(offsets.items())
        if marker in object_bytes(data, offset)
    ]


def stream_bytes(data: bytes, offset: int) -> bytes:
    """Raw bytes between ``stream\\n`` and ``\\nendstream`` of the object at ``offset``."""
    match = _STREAM_HEADER_RE.search(data, offset)
    if match is None:
        raise AssertionError(f"no stream keyword in object at offset {offset}")
    start = match.end()
    end = data.index(b"\nendstream", start)
    return data[start:end]


def inflate_stream(data: bytes, offset: int) -> bytes:
    """Extract the object's stream body and inflate it (FlateDecode)."""
    return zlib.decompress(stream_bytes(data, offset))


def refs_in(raw: bytes) -> List[int]:
    """All ``N 0 R`` references inside ``raw``, in document order."""
    return [int(number) for number in _REF_RE.findall(raw)]


def is_stream_object(data: bytes, offsets: Dict[int, int], obj_id: int) -> bool:
    """True when the object's body contains a ``stream`` keyword."""
    return b"stream\n" in object_bytes(data, offsets[obj_id])


def stream_refs_in(data: bytes, offsets: Dict[int, int], raw: bytes) -> List[int]:
    """Refs inside ``raw`` that point at objects whose body is a stream."""
    return [ref for ref in refs_in(raw) if is_stream_object(data, offsets, ref)]


# ---------------------------------------------------------------------------
# Minimal ASN.1 DER reader (phase 7.4: CMS signature verification in tests)
# ---------------------------------------------------------------------------

#: DER tag byte values the reader understands.
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_OID = 0x06
TAG_UTCTIME = 0x17
TAG_SEQUENCE = 0x30
TAG_SET = 0x31


def der_read_element(data: bytes, offset: int = 0) -> Tuple[int, bytes, int]:
    """Read one DER TLV at ``offset``; returns ``(tag, value, next_offset)``.

    ``value`` is the content octets (without tag/length); ``next_offset``
    is where the next sibling starts.  Long-form lengths are handled; the
    tag is returned raw (including context-specific bits like ``0xA0``).
    """
    if offset + 2 > len(data):
        raise AssertionError("truncated DER element")
    tag = data[offset]
    length = data[offset + 1]
    pos = offset + 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or pos + count > len(data):
            raise AssertionError(f"malformed DER length at offset {offset}")
        length = int.from_bytes(data[pos : pos + count], "big")
        pos += count
    end = pos + length
    if end > len(data):
        raise AssertionError(f"DER element overruns buffer at offset {offset}")
    return tag, data[pos:end], end


def der_children(value: bytes) -> List[Tuple[int, bytes, int]]:
    """Split a constructed DER value into its children (tag, value, next)."""
    children = []
    offset = 0
    while offset < len(value):
        tag, child_value, offset = der_read_element(value, offset)
        children.append((tag, child_value, offset))
    return children


def der_oid_from_value(value: bytes) -> str:
    """Decode an OBJECT IDENTIFIER's content octets to dotted-decimal."""
    if not value:
        raise AssertionError("empty OID")
    first = value[0]
    arcs = [first // 40, first % 40]
    current = 0
    for byte in value[1:]:
        current = (current << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(current)
            current = 0
    return ".".join(str(arc) for arc in arcs)


def der_find_child(children: List[Tuple[int, bytes, int]], tag: int) -> Optional[bytes]:
    """The first child value whose raw tag equals ``tag`` (None when absent)."""
    for child_tag, child_value, _next in children:
        if child_tag == tag:
            return child_value
    return None


def der_int_from_value(value: bytes) -> int:
    """Decode an INTEGER's content octets (non-negative, no 0x00 prefix issue)."""
    return int.from_bytes(value, "big")


def der_encode_tlv(tag: int, value: bytes) -> bytes:
    """Encode a DER tag/length/value triplet (mirror of engine.crypto)."""
    if len(value) < 0x80:
        return bytes([tag, len(value)]) + value
    raw = len(value).to_bytes((len(value).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(raw)]) + raw + value

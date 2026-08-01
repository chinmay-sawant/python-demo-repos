"""Low-level PDF syntax encoding: numbers, names, strings, dictionaries,
arrays, hex strings, stream objects, the classic xref section and the
trailer.

This module owns byte-level encoding only; it has no knowledge of document
semantics (page trees, fonts, compliance).  All encode functions return
``bytes`` and never write directly; the :class:`ByteWriter` is the single
place where bytes are appended and byte offsets are tracked.

Phase 6 adds the performance & pooling layer: bounded LRU caches for name
and number formatting (repeat-heavy keys like ``/Type``, ``/S``, ``/K``
are encoded once per process), a fast whole-string regular-name check that
avoids the per-character regex, preallocatable/reusable :class:`ByteWriter`
buffers for the final PDF and a list-based xref encoder so a document can
reuse its offset list between renders.

Indirect references are represented by the :class:`ObjectId` value type,
which doubles as the marker that renders to ``N 0 R`` inside dictionaries.
Names are represented by :class:`PdfName` (a ``str`` subclass normalising to
a leading ``/``); the :func:`N` helper builds them concisely.  Hex strings
(e.g. the two ``/ID`` entries) are represented by :class:`PdfHexString`,
a ``bytes`` subclass.
"""

from __future__ import annotations

import datetime
import os
import re
import zlib
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Union

__all__ = [
    "B",
    "ByteWriter",
    "FixedDigits",
    "N",
    "ObjectId",
    "PdfBool",
    "PdfHexString",
    "PdfName",
    "compressed_stream",
    "encode_array",
    "encode_dict",
    "encode_hex_string",
    "encode_name",
    "encode_object",
    "encode_stream_object",
    "encode_string",
    "encode_value",
    "encode_xref_section",
    "escape_name",
    "escape_string",
    "format_date",
    "format_number",
    "format_number_bytes",
]

# Characters that may appear unescaped inside a PDF name (ISO 32000-2 table 4).
# Note: '#' is deliberately absent -- it is the escape introducer.
_REGULAR_NAME_RE = re.compile(r"^[!$&'*+,\-.\dA-Za-z0-9;=?@^_`~|]+$")

# The regular-name characters as a byte set, used by the fast whole-string
# check in :func:`escape_name` (avoids the per-character regex on the hot
# path -- every cell element name, dictionary key and font resource name
# passes through here).
_REGULAR_NAME_BYTES = frozenset(
    b"!$&'*+,-.0123456789;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ^_`"
    b"abcdefghijklmnopqrstuvwxyz|~"
)
_IRREGULAR_NAME_BYTES = bytes(
    byte for byte in range(256) if byte not in _REGULAR_NAME_BYTES
)

# Bounded per-process caches.  Names and numbers repeat heavily across a
# dense document (per-cell keys, per-row coordinates), so encoding each
# distinct value once avoids the formatting churn without growing without
# bound.  Both are pure functions of their keys, so the caches never change
# the emitted bytes.
_ENCODE_NAME_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_ENCODE_NAME_CACHE_MAX = 1024
_FORMAT_NUMBER_CACHE: "OrderedDict[Union[int, float], str]" = OrderedDict()
_FORMAT_NUMBER_CACHE_MAX = 2048
_NUMBER_BYTES_CACHE: "OrderedDict[Union[int, float], bytes]" = OrderedDict()
_NUMBER_BYTES_CACHE_MAX = 2048
_REF_CACHE: "OrderedDict[int, bytes]" = OrderedDict()
_REF_CACHE_MAX = 2048


def _cache_set(cache: OrderedDict, key: Any, value: Any, maximum: int) -> None:
    """Insert into an LRU ``cache``, evicting the oldest entry past ``maximum``."""
    cache[key] = value
    if len(cache) > maximum:
        cache.popitem(last=False)


class PdfName(str):
    """A PDF name object; normalises to a leading ``/`` (e.g. ``Type``)."""

    __slots__ = ()

    def __new__(cls, name: str) -> "PdfName":
        if not name.startswith("/"):
            name = "/" + name
        return super().__new__(cls, name)


def N(name: str) -> PdfName:
    """Shorthand for :class:`PdfName`, for compact dictionary construction."""
    return PdfName(name)


class PdfBool:
    """A PDF boolean object (``true`` / ``false``).

    Python ``bool`` stays rejected by the encoders (PDF historically has no
    native boolean in this codebase's encoders), so callers that need a
    ``true``/``false`` value (e.g. ``/Marked true``) wrap it explicitly.
    """

    __slots__ = ("value",)

    def __init__(self, value: bool) -> None:
        self.value = bool(value)

    def __repr__(self) -> str:
        return "PdfBool(%s)" % ("true" if self.value else "false")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PdfBool) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)


def B(value: bool) -> PdfBool:
    """Shorthand for :class:`PdfBool` (``B(True)`` renders as ``true``)."""
    return PdfBool(value)


class FixedDigits(int):
    """An integer rendered as a fixed-width zero-padded decimal field.

    Phase 7.4: the signature dictionary's ``/ByteRange`` array must occupy
    a byte-range of constant length before and after signing (the signer
    rewrites the four offsets in place after the document is rendered, and
    the file length must not change).  Ten digits cover files up to
    9,999,999,999 bytes; PDF numbers may carry leading zeros, so the
    placeholder ``0000000000`` is a valid ``0``.
    """

    __slots__ = ()

    WIDTH = 10

    @classmethod
    def render(cls, value: int) -> bytes:
        """The fixed-width ASCII rendering of ``value`` (cacheable helper)."""
        return b"%0*d" % (cls.WIDTH, value)


class PdfHexString(bytes):
    """A PDF hex string ``<...>``; the bytes are rendered as lowercase hex."""

    __slots__ = ()


class ObjectId:
    """An indirect object identifier, allocated by the document builder.

    Renders as an indirect reference ``N 0 R``; the same value is used as the
    marker for indirect references inside encoded dictionaries.

    Phase 6: rendered references are cached per number (an LRU, bounded) --
    dense documents reference the same objects from their kids arrays,
    parents and page trees dozens of times each, and ``b"%d 0 R"`` is pure.
    """

    __slots__ = ("number",)

    def __init__(self, number: int) -> None:
        if number < 1:
            raise ValueError(f"object numbers are 1-based, got {number}")
        self.number = number

    def render_ref(self) -> bytes:
        """Encode as an indirect reference, e.g. ``b"3 0 R"`` (cached)."""
        cached = _REF_CACHE.get(self.number)
        if cached is not None:
            _REF_CACHE.move_to_end(self.number)
            return cached
        encoded = b"%d 0 R" % self.number
        _cache_set(_REF_CACHE, self.number, encoded, _REF_CACHE_MAX)
        return encoded

    def __repr__(self) -> str:
        return f"ObjectId({self.number})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ObjectId) and self.number == other.number

    def __hash__(self) -> int:
        return hash(self.number)


def format_number(value: Union[int, float]) -> str:
    """Format a number without unnecessary digits (``1.0`` -> ``1``).

    PDF 2.0 numbers are plain decimal notation; exponential forms are never
    emitted, and integral floats render without a decimal point.  Results
    are cached per value (a pure function of ``value``), which keeps dense
    documents -- where every row re-encodes the same coordinates and sizes
    -- from re-formatting the same numbers.
    """
    if isinstance(value, bool):
        raise TypeError("PDF has no boolean object type")
    cached = _FORMAT_NUMBER_CACHE.get(value)
    if cached is not None:
        _FORMAT_NUMBER_CACHE.move_to_end(value)
        return cached
    if isinstance(value, int):
        text = str(value)
    elif value.is_integer():
        text = str(int(value))
    else:
        text = repr(value)
        if "e" in text or "E" in text:
            text = "%.6f" % value
            text = text.rstrip("0").rstrip(".")
    _cache_set(_FORMAT_NUMBER_CACHE, value, text, _FORMAT_NUMBER_CACHE_MAX)
    return text


def format_number_bytes(value: Union[int, float]) -> bytes:
    """Encode a number as ASCII bytes (the hot-path :func:`format_number`)."""
    if isinstance(value, bool):
        raise TypeError("PDF has no boolean object type")
    cached = _NUMBER_BYTES_CACHE.get(value)
    if cached is not None:
        _NUMBER_BYTES_CACHE.move_to_end(value)
        return cached
    encoded = format_number(value).encode("ascii")
    _cache_set(_NUMBER_BYTES_CACHE, value, encoded, _NUMBER_BYTES_CACHE_MAX)
    return encoded


def format_date(when: datetime.datetime) -> str:
    """Format a datetime as a PDF date string ``D:YYYYMMDDHHmmSS``."""
    return "D:" + when.strftime("%Y%m%d%H%M%S")


def escape_string(data: bytes) -> bytes:
    """Escape raw bytes for a PDF string literal ``(...)``.

    Backslash, parentheses and non-printable/high bytes become ``\\``,
    ``\\(``, ``\\)`` or ``\\ddd`` octal escapes.
    """
    out = bytearray()
    for byte in data:
        if byte == 0x5C:  # backslash
            out.extend(b"\\\\")
        elif byte == 0x28:  # "("
            out.extend(b"\\(")
        elif byte == 0x29:  # ")"
            out.extend(b"\\)")
        elif byte < 0x20 or byte >= 0x7F:
            out.extend(b"\\%03o" % byte)
        else:
            out.append(byte)
    return bytes(out)


def encode_string(value: Union[str, bytes]) -> bytes:
    """Encode a string literal ``(...)``; ``str`` input is UTF-8 encoded.

    Note: standard Type1 fonts expect PDFDocEncoding/WinAnsi text in the
    content stream, so keep content text ASCII until font embedding lands.
    """
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return b"(" + escape_string(raw) + b")"


def escape_name(raw: str) -> bytes:
    """Escape the characters of a name's suffix (without the leading ``/``).

    Any character outside the regular name set (including ``#``) becomes a
    ``#XX`` hex escape with uppercase hex digits.

    The fast path checks the whole string in one C-level ``translate``
    (rejecting anything outside the regular set) instead of running the
    per-character regex, which dominates object-body encoding for dense
    tagged documents.
    """
    try:
        encoded = raw.encode("ascii")
    except UnicodeEncodeError:
        encoded = None
    if encoded is not None:
        kept = encoded.translate(None, _IRREGULAR_NAME_BYTES)
        if len(kept) == len(encoded):
            return encoded
    out = bytearray()
    for ch in raw:
        if len(ch) == 1 and 0x21 <= ord(ch) <= 0x7E and _REGULAR_NAME_RE.match(ch):
            out.extend(ch.encode("ascii"))
        else:
            out.extend(b"#" + ("%02X" % ord(ch)).encode("ascii"))
    return bytes(out)


def encode_name(name: str) -> bytes:
    """Encode a PDF name, e.g. ``F1`` -> ``b"/F1"`` (a leading ``/`` is tolerated).

    Encoded results are cached per name string: dense documents repeat the
    same handful of keys (``Type``, ``S``, ``P``, ``K``, ``Pg``, ...) tens
    of thousands of times, and the cache turns each one into a dict lookup.
    """
    cached = _ENCODE_NAME_CACHE.get(name)
    if cached is not None:
        _ENCODE_NAME_CACHE.move_to_end(name)
        return cached
    suffix = name[1:] if name.startswith("/") else name
    encoded = b"/" + escape_name(suffix)
    _cache_set(_ENCODE_NAME_CACHE, name, encoded, _ENCODE_NAME_CACHE_MAX)
    return encoded


def encode_hex_string(data: bytes) -> bytes:
    """Encode a hex string ``<...>`` with lowercase hex digits."""
    return b"<" + data.hex().encode("ascii") + b">"


def encode_array(items: List[Any]) -> bytes:
    """Encode a PDF array ``[ ... ]`` of encoded values."""
    return b"[" + b" ".join(encode_value(item) for item in items) + b"]"


def encode_dict(pairs: Dict[Any, Any]) -> bytes:
    """Encode a dictionary ``<< /Key value ... >>`` preserving key order."""
    chunks: List[bytes] = []
    for key, value in pairs.items():
        if not isinstance(key, str):
            raise TypeError(f"dictionary keys must be names, got {type(key).__name__}")
        chunks.append(encode_name(key))
        chunks.append(encode_value(value))
    return b"<< " + b" ".join(chunks) + b" >>"


def encode_value(value: Any) -> bytes:
    """Encode any supported Python value as PDF syntax.

    Dispatch table: ``ObjectId`` -> indirect ref, ``PdfName`` -> name,
    ``PdfHexString`` -> hex string, ``str``/``bytes`` -> literal string,
    ``int``/``float`` -> number, ``list``/``tuple`` -> array, ``dict`` ->
    dictionary, ``None`` -> ``null`` (phase 7: the ``/XYZ`` destination
    zoom slot and other "no value" slots).  Anything else raises
    ``TypeError``.

    Exact ``type(value) is ...`` checks run first for the hot dense-table /
    structure-tree shapes (dict, list, ObjectId, PdfName, int) so the
    multi-million ``isinstance`` cascade seen in cProfile is avoided on the
    common path.  Subclass and uncommon types still fall through to
    ``isinstance``.
    """
    if value is None:
        return b"null"
    t = type(value)
    # Hot path order matches HFT/dense dumps: nested dicts and arrays of
    # refs/names/numbers dominate encode_value call counts.
    if t is dict:
        return encode_dict(value)
    if t is list:
        return encode_array(value)
    if t is tuple:
        return encode_array(list(value))
    if t is ObjectId:
        return value.render_ref()
    if t is PdfName:
        return encode_name(value)
    if t is int:
        return format_number_bytes(value)
    if t is float:
        return format_number_bytes(value)
    if t is str:
        return encode_string(value)
    if t is bytes:
        return encode_string(value)
    if t is PdfBool:
        return b"true" if value.value else b"false"
    if t is PdfHexString:
        return encode_hex_string(value)
    if t is FixedDigits:
        return FixedDigits.render(int(value))
    if t is bool:
        raise TypeError("PDF has no boolean object type")
    # Subclass / uncommon fall-through (keeps API permissive).
    if isinstance(value, ObjectId):
        return value.render_ref()
    if isinstance(value, PdfBool):
        return b"true" if value.value else b"false"
    if isinstance(value, PdfHexString):
        return encode_hex_string(value)
    if isinstance(value, PdfName):
        return encode_name(value)
    if isinstance(value, FixedDigits):
        return FixedDigits.render(int(value))
    if isinstance(value, str):
        return encode_string(value)
    if isinstance(value, bytes):
        return encode_string(value)
    if isinstance(value, bool):
        raise TypeError("PDF has no boolean object type")
    if isinstance(value, (int, float)):
        return format_number_bytes(value)
    if isinstance(value, (list, tuple)):
        return encode_array(list(value))
    if isinstance(value, dict):
        return encode_dict(value)
    raise TypeError(f"cannot encode value of type {type(value).__name__}")

def encode_object(obj_id: int, value: Any) -> bytes:
    """Encode a complete indirect object ``N 0 obj ... endobj``."""
    return b"%d 0 obj\n" % obj_id + encode_value(value) + b"\nendobj\n"


def compressed_stream(data: bytes) -> bytes:
    """Compress stream data with zlib (used with ``/Filter /FlateDecode``).

    The document builder enables compression by default and carries a flag
    to disable it; this helper keeps that decision out of the byte-level
    encoders.
    """
    return zlib.compress(data)


def encode_stream_object(
    obj_id: int,
    data: bytes,
    stream_dict: Optional[Dict[Any, Any]] = None,
) -> bytes:
    """Encode a complete stream object ``N 0 obj << /Length n >> stream ... endstream endobj``.

    ``/Length`` is always set to the exact byte length of ``data`` and wins
    over any caller-supplied value; extra entries (e.g. ``/Filter
    /FlateDecode`` in later phases) are passed via ``stream_dict``.
    """
    extra = dict(stream_dict) if stream_dict is not None else {}
    extra[PdfName("Length")] = len(data)
    body = encode_dict(extra) + b"\nstream\n" + data + b"\nendstream\nendobj\n"
    return b"%d 0 obj\n" % obj_id + body


def encode_xref_section(offsets: Union[Dict[int, int], Sequence[int]], size: int) -> bytes:
    """Encode a classic xref section with one subsection covering objects 0..size-1.

    Object 0 is the free-list head (``0000000000 65535 f``); every other
    object must have an offset in ``offsets`` or ``ValueError`` is raised.
    ``offsets`` may be a ``{obj number: offset}`` dict or a list/sequence
    indexed by object number (the phase-6 pooled form), which lets the
    document reuse one list across renders instead of allocating a dict per
    render.
    """
    lines: List[bytes] = [b"xref\n", b"0 %d\n" % size, b"0000000000 65535 f \n"]
    for obj_id in range(1, size):
        if isinstance(offsets, dict):
            try:
                offset = offsets[obj_id]
            except KeyError:
                raise ValueError(f"missing byte offset for object {obj_id}") from None
        else:
            offset = offsets[obj_id]
            if not offset:
                raise ValueError(f"missing byte offset for object {obj_id}")
        lines.append(b"%010d %05d n \n" % (offset, 0))
    return b"".join(lines)


class ByteWriter:
    """Accumulates output bytes while tracking the current byte offset.

    The offset is the position in the final file; the emit path records it
    for every indirect object so the xref section can be built afterwards.

    Phase 6: the underlying ``bytearray`` can be preallocated (``prealloc``
    bytes of capacity up front, so a size-known render never reallocates
    mid-write) and can be overridden/reused by passing ``buffer`` -- a
    document keeps its pool between renders and hands it back in.  Both
    modes track the used length separately from the buffer capacity, so a
    pooled buffer larger than the next document's output still returns
    exactly the bytes written.  With ``ENGINE_DEBUG_BUFFERS=1`` the writer
    tracks its length high-water mark, exposed via :meth:`buffer_stats`.
    """

    __slots__ = ("_buffer", "_debug", "_high_water", "_tracked", "_used")

    def __init__(
        self, buffer: Optional[bytearray] = None, *, prealloc: int = 0
    ) -> None:
        if buffer is not None:
            self._buffer = buffer
            self._tracked = True
        elif prealloc > 0:
            self._buffer = bytearray(prealloc)
            self._tracked = True
        else:
            self._buffer = bytearray()
            self._tracked = False
        self._used = 0
        self._debug = os.environ.get("ENGINE_DEBUG_BUFFERS") == "1"
        self._high_water = 0

    def tell(self) -> int:
        """Return the current byte offset (length of everything written so far)."""
        return self._used

    def write(self, data: bytes) -> int:
        """Append ``data`` and return the offset at which it started."""
        offset = self._used
        if self._tracked:
            end = offset + len(data)
            if end > len(self._buffer):
                self._buffer.extend(b"\x00" * (end - len(self._buffer)))
            self._buffer[offset:end] = data
            self._used = end
        else:
            self._buffer.extend(data)
            self._used = len(self._buffer)
        if self._debug and self._used > self._high_water:
            self._high_water = self._used
        return offset

    def getvalue(self) -> bytes:
        """Return all bytes written so far."""
        return bytes(self._buffer[: self._used])

    def take_buffer(self) -> bytearray:
        """Detach and return the underlying buffer (for pooling between renders).

        The writer becomes unusable afterwards; hand the returned buffer to
        a fresh :class:`ByteWriter` to reuse its allocation.
        """
        buffer = self._buffer
        self._buffer = bytearray()
        self._used = 0
        self._tracked = False
        return buffer

    def feed_digest(self, digest: Any) -> None:
        """Feed every byte written so far into ``digest`` without copying."""
        digest.update(self._buffer[: self._used])

    def buffer_stats(self) -> Dict[str, int]:
        """Debug stats: current length and the length high-water mark.

        Only meaningful when ``ENGINE_DEBUG_BUFFERS=1`` (otherwise the
        high-water mark is not tracked and stays 0).
        """
        return {"length": self._used, "high_water": self._high_water}

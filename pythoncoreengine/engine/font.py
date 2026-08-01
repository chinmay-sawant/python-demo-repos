"""TrueType font parsing, glyph subsetting, the font registry and the Liberation map.

Phase 3 owns everything between a standard font request (``Helvetica``,
``Times-Roman``, ...) and the embedded Type0/CIDFontType2 chain that PDF/A-4
requires: a pure-stdlib ``struct``-based TTF parser (:class:`TTFFont`), a
glyph subsetter that rebuilds a valid TTF containing only the glyphs a
document actually used (:class:`TTFSubsetter`), the per-document registry
that tracks character usage and generates subsets once all content is known
(:class:`FontRegistry`) and the Liberation font substitution map.

Emitted object chain for one embedded font (six indirect objects):

* Type0 font dict (``/Subtype /Type0``, ``/Encoding /Identity-H``,
  ``/DescendantFonts``, ``/ToUnicode``)
* CIDFont (``/Subtype /CIDFontType2``, ``/CIDSystemInfo``, ``/FontDescriptor``,
  ``/DW``, ``/W``, ``/CIDToGIDMap /Identity``)
* FontDescriptor (``/FontName``, metrics, ``/FontFile2``, ``/CIDSet``)
* FontFile2 stream: the FlateDecode-compressed subset TTF
* CIDSet stream: one bit per subset glyph (required by PDF/A)
* ToUnicode stream: a ``/Adobe-Identity-UCS`` CMap mapping CIDs to UTF-16BE

Everything is deterministic: subsets iterate sorted characters and sorted
glyph IDs, and the subset tag derives from an md5 of the face name.

Phase 6 adds the optional subset cache: built subset TTF bytes are cached
per process keyed by ``(font path, frozenset(chars))`` in a bounded LRU
(``_SUBSET_BYTES_CACHE``, max 8 entries) so repeat renders of identical
subsets skip the expensive sfnt assembly and checksums.  The cache is the
only process-level state in the engine; it is keyed safely (path + used
chars, never document objects) and returns immutable bytes, so per-doc
isolation is preserved.
"""

from __future__ import annotations

import hashlib
import struct
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from .write import N, ObjectId, PdfName, compressed_stream

__all__ = [
    "FontChain",
    "FontEntry",
    "FontRegistry",
    "LIBERATION_FONT_DIR",
    "LIBERATION_FONT_PATHS",
    "STANDARD_TO_LIBERATION",
    "TTFFont",
    "TTFSubsetter",
    "standard_font_name",
]

# ---------------------------------------------------------------------------
# Liberation substitution map
# ---------------------------------------------------------------------------

LIBERATION_FONT_DIR = Path("/usr/share/fonts/truetype/liberation")

_LIBERATION_STYLES = ("Regular", "Bold", "Italic", "BoldItalic")
LIBERATION_FONT_PATHS: Dict[str, Path] = {
    name: LIBERATION_FONT_DIR / f"{name}.ttf"
    for family in ("Sans", "Serif", "Mono")
    for name in (f"Liberation{family}-{style}" for style in _LIBERATION_STYLES)
}

STANDARD_TO_LIBERATION: Dict[str, str] = {
    "Helvetica": "LiberationSans-Regular",
    "Helvetica-Bold": "LiberationSans-Bold",
    "Helvetica-Oblique": "LiberationSans-Italic",
    "Helvetica-BoldOblique": "LiberationSans-BoldItalic",
    "Times-Roman": "LiberationSerif-Regular",
    "Times-Bold": "LiberationSerif-Bold",
    "Times-Italic": "LiberationSerif-Italic",
    "Times-BoldItalic": "LiberationSerif-BoldItalic",
    "Courier": "LiberationMono-Regular",
    "Courier-Bold": "LiberationMono-Bold",
    "Courier-Oblique": "LiberationMono-Italic",
    "Courier-BoldOblique": "LiberationMono-BoldItalic",
}

_STANDARD_FONT_NAMES = frozenset(STANDARD_TO_LIBERATION) | {"Symbol", "ZapfDingbats"}
_STANDARD_FAMILY_NAMES = {"Helvetica", "Times", "Times-Roman", "Courier"}


def standard_font_name(family: str, *, bold: bool = False, italic: bool = False) -> str:
    """Compose a standard font name from a family and style flags.

    ``("Times", italic=True)`` -> ``Times-Italic``; ``("Helvetica",
    bold=True, italic=True)`` -> ``Helvetica-BoldOblique``.  Already-styled
    standard names (e.g. ``Times-Italic``) pass through unchanged.
    """
    family = family.strip()
    if family in _STANDARD_FAMILY_NAMES:
        if family in ("Times", "Times-Roman"):
            if not bold and not italic:
                return "Times-Roman"
            style = ("Bold" if bold else "") + ("Italic" if italic else "")
            return "Times-" + style
        style = ("Bold" if bold else "") + ("Oblique" if italic else "")
        return family if not style else family + "-" + style
    if family in _STANDARD_FONT_NAMES:
        return family
    raise ValueError(f"unsupported standard font family {family!r}")


# ---------------------------------------------------------------------------
# TrueType parsing (sfnt, head, hhea, maxp, hmtx, cmap, loca, glyf, post, OS/2)
# ---------------------------------------------------------------------------

_SFNT_VERSION = struct.Struct(">IHHHH")
_TABLE_RECORD = struct.Struct(">4sIII")

# Head table layout (offsets within the 54-byte table).
_HEAD_UNITS_PER_EM = 18
_HEAD_CHECKSUM_ADJUSTMENT = 8
_HEAD_BBOX = 36  # xMin, yMin, xMax, yMax as four int16
_HEAD_INDEX_TO_LOC_FORMAT = 50

# Hhea table offsets.
_HHEA_ASCENT = 4
_HHEA_DESCENT = 6
_HHEA_ADVANCE_WIDTH_MAX = 10
_HHEA_NUMBER_OF_HMETRICS = 34

_MAXP_NUM_GLYPHS = 4

_CMAP_VERSION_AND_COUNT = 2
_CMAP_ENCODING_RECORD = 8
_POST_ITALIC_ANGLE = 4
_POST_UNDERLINE_POSITION = 8
_POST_UNDERLINE_THICKNESS = 10
_POST_IS_FIXED_PITCH = 12

_OS2_VERSION = 0
_OS2_WEIGHT_CLASS = 4
_OS2_TYPO_ASCENDER = 68
_OS2_TYPO_DESCENDER = 70
_OS2_X_HEIGHT = 86
_OS2_CAP_HEIGHT = 88

# Composite glyph flags.
_GLYF_ARG_1_AND_2_ARE_WORDS = 0x0001
_GLYF_WE_HAVE_A_SCALE = 0x0008
_GLYF_MORE_COMPONENTS = 0x0020
_GLYF_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_GLYF_WE_HAVE_A_TWO_BY_TWO = 0x0080
_GLYF_WE_HAVE_INSTRUCTIONS = 0x0100

# A complete assembled font must sum (as uint32 words) to this value.
_WHOLE_FONT_CHECKSUM = 0xB1B0AFBA
# Subset tag alphabet: 16 uppercase letters so tags are all-letter.
_TAG_ALPHABET = "ABCDEFGHIJKLMNOP"

# Bounded per-process cache of built subset TTF bytes, keyed by
# ``(font path, frozenset(used chars))`` (phase 6).  The cache holds
# immutable bytes only, keyed on path + chars rather than document state,
# so repeat renders of identical subsets skip the sfnt assembly while
# per-document isolation is preserved.
_SUBSET_BYTES_CACHE: "OrderedDict[Tuple[Any, frozenset], bytes]" = OrderedDict()
_SUBSET_BYTES_CACHE_MAX = 8


class TTFFont:
    """A parsed TrueType font (stdlib ``struct`` only; no name table needed).

    Parses sfnt header, table directory and the tables required for subsetting
    and PDF metric extraction: ``head``, ``hhea``, ``maxp``, ``hmtx``, ``cmap``
    (formats 4, 6 and 12), ``loca`` (short/long), ``glyf`` (including composite
    glyph component walks), ``post`` and ``OS/2``.  The ``name`` table is not
    parsed; PDF font names are synthesised from the face name.
    """

    def __init__(self, data: bytes, source: str = "<bytes>") -> None:
        if len(data) < 12:
            raise ValueError(f"font {source}: too short for an sfnt header")
        version = data[:4]
        if version == b"OTTO":
            raise ValueError(f"font {source}: CFF-flavoured OpenType is not supported")
        if version not in (b"\x00\x01\x00\x00", b"true"):
            raise ValueError(f"font {source}: unsupported sfnt version {version!r}")
        self.source = source
        self._data = data
        self._tables: Dict[str, Tuple[int, int]] = {}
        num_tables = struct.unpack(">H", data[4:6])[0]
        directory_end = 12 + num_tables * 16
        if directory_end > len(data):
            raise ValueError(f"font {source}: truncated table directory")
        for index in range(num_tables):
            tag, _checksum, offset, length = _TABLE_RECORD.unpack_from(
                data, 12 + index * 16
            )
            if offset + length > len(data):
                raise ValueError(f"font {source}: table {tag!r} out of bounds")
            self._tables[tag.decode("ascii", "replace")] = (offset, length)
        self._parse_head()
        self._parse_hhea()
        self._parse_maxp()
        self._parse_hmtx()
        self._parse_cmap()
        self._parse_loca()
        self._parse_post()
        self._parse_os2()

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "TTFFont":
        """Parse the TTF file at ``path`` (name for errors taken from it)."""
        path = Path(path)
        return cls(path.read_bytes(), str(path))

    # ------------------------------------------------------------------
    # Table access
    # ------------------------------------------------------------------

    def _table(self, tag: str) -> Tuple[int, int]:
        try:
            return self._tables[tag]
        except KeyError:
            raise ValueError(
                f"font {self.source}: missing required table {tag!r}"
            ) from None

    def table_bytes(self, tag: str) -> bytes:
        """Return the raw bytes of a table (raises for unknown tags)."""
        offset, length = self._table(tag)
        return self._data[offset : offset + length]

    def _unpack(self, fmt: str, tag: str, offset: int) -> Tuple[Any, ...]:
        base, _length = self._table(tag)
        return struct.unpack_from(fmt, self._data, base + offset)

    # ------------------------------------------------------------------
    # Table parsers
    # ------------------------------------------------------------------

    def _parse_head(self) -> None:
        (self.units_per_em,) = self._unpack(">H", "head", _HEAD_UNITS_PER_EM)
        (self.x_min, self.y_min, self.x_max, self.y_max) = self._unpack(
            ">4h", "head", _HEAD_BBOX
        )
        (self.index_to_loc_format,) = self._unpack(
            ">h", "head", _HEAD_INDEX_TO_LOC_FORMAT
        )
        if self.index_to_loc_format not in (0, 1):
            raise ValueError(f"font {self.source}: bad indexToLocFormat")

    def _parse_hhea(self) -> None:
        (self.ascent, self.descent, _line_gap) = self._unpack(
            ">3h", "hhea", _HHEA_ASCENT
        )
        (self.number_of_hmetrics,) = self._unpack(
            ">H", "hhea", _HHEA_NUMBER_OF_HMETRICS
        )

    def _parse_maxp(self) -> None:
        (self.num_glyphs,) = self._unpack(">H", "maxp", _MAXP_NUM_GLYPHS)
        if self.num_glyphs == 0:
            raise ValueError(f"font {self.source}: maxp reports zero glyphs")

    def _parse_hmtx(self) -> None:
        self.advances: List[int] = []
        self.left_side_bearings: List[int] = []
        for glyph in range(self.number_of_hmetrics):
            advance, lsb = self._unpack(">Hh", "hmtx", glyph * 4)
            self.advances.append(advance)
            self.left_side_bearings.append(lsb)
        base = self.number_of_hmetrics * 4
        for glyph in range(self.number_of_hmetrics, self.num_glyphs):
            (lsb,) = self._unpack(">h", "hmtx", base + (glyph - self.number_of_hmetrics) * 2)
            self.advances.append(self.advances[-1])
            self.left_side_bearings.append(lsb)

    def _parse_cmap(self) -> None:
        (num_tables,) = self._unpack(">H", "cmap", _CMAP_VERSION_AND_COUNT)
        candidates: List[Tuple[int, int]] = []
        for index in range(num_tables):
            _platform, _encoding, offset = self._unpack(
                ">HHI", "cmap", 4 + index * _CMAP_ENCODING_RECORD
            )
            (fmt,) = self._unpack(">H", "cmap", offset)
            if fmt in (4, 6, 12):
                candidates.append((fmt, offset))
        if not candidates:
            raise ValueError(f"font {self.source}: no supported cmap subtable")
        mapping: Dict[int, int] = {}
        for fmt, offset in candidates:
            if fmt == 4:
                mapping.update(self._cmap_format_4(offset))
            elif fmt == 6:
                mapping.update(self._cmap_format_6(offset))
            else:
                mapping.update(self._cmap_format_12(offset))
        self.cmap = mapping

    def _cmap_format_4(self, offset: int) -> Dict[int, int]:
        (seg_count_x2,) = self._unpack(">H", "cmap", offset + 6)
        seg_count = seg_count_x2 // 2
        end_codes = self._unpack(">%dH" % seg_count, "cmap", offset + 14)
        start_codes = self._unpack(">%dH" % seg_count, "cmap", offset + 16 + seg_count_x2)
        id_deltas = self._unpack(">%dh" % seg_count, "cmap", offset + 16 + seg_count_x2 * 2)
        id_range_offsets = self._unpack(
            ">%dH" % seg_count, "cmap", offset + 16 + seg_count_x2 * 3
        )
        glyph_array_offset = offset + 16 + seg_count_x2 * 4
        mapping: Dict[int, int] = {}
        for index in range(seg_count):
            if start_codes[index] == 0xFFFF and end_codes[index] == 0xFFFF:
                continue
            for code in range(start_codes[index], end_codes[index] + 1):
                if id_range_offsets[index] == 0:
                    gid = (code + id_deltas[index]) & 0xFFFF
                else:
                    position = (
                        index
                        + id_range_offsets[index] // 2
                        + (code - start_codes[index])
                        - seg_count
                    )
                    (gid,) = self._unpack(">H", "cmap", glyph_array_offset + 2 * position)
                    if gid != 0:
                        gid = (gid + id_deltas[index]) & 0xFFFF
                if gid != 0:
                    mapping[code] = gid
        return mapping

    def _cmap_format_6(self, offset: int) -> Dict[int, int]:
        (first_code,) = self._unpack(">H", "cmap", offset + 6)
        (entry_count,) = self._unpack(">H", "cmap", offset + 8)
        glyph_ids = self._unpack(">%dH" % entry_count, "cmap", offset + 10)
        return {
            first_code + index: gid
            for index, gid in enumerate(glyph_ids)
            if gid != 0
        }

    def _cmap_format_12(self, offset: int) -> Dict[int, int]:
        (num_groups,) = self._unpack(">I", "cmap", offset + 12)
        mapping: Dict[int, int] = {}
        for index in range(num_groups):
            start, end, start_gid = self._unpack(">III", "cmap", offset + 16 + index * 12)
            for code in range(start, end + 1):
                gid = start_gid + (code - start)
                if gid != 0:
                    mapping[code] = gid
        return mapping

    def _parse_loca(self) -> None:
        if self.index_to_loc_format == 0:
            self._loca_offsets = [
                self._unpack(">H", "loca", 2 * index)[0] * 2
                for index in range(self.num_glyphs + 1)
            ]
        else:
            self._loca_offsets = [
                self._unpack(">I", "loca", 4 * index)[0]
                for index in range(self.num_glyphs + 1)
            ]

    def _parse_post(self) -> None:
        if "post" in self._tables:
            (self.italic_angle,) = self._unpack(">i", "post", _POST_ITALIC_ANGLE)
            (self.underline_position,) = self._unpack(">h", "post", _POST_UNDERLINE_POSITION)
            (self.underline_thickness,) = self._unpack(
                ">h", "post", _POST_UNDERLINE_THICKNESS
            )
            (self.is_fixed_pitch,) = self._unpack(">I", "post", _POST_IS_FIXED_PITCH)
        else:
            self.italic_angle = 0
            self.underline_position = -100
            self.underline_thickness = 50
            self.is_fixed_pitch = 0

    def _parse_os2(self) -> None:
        if "OS/2" not in self._tables:
            self._os2_version = 0
            self.us_weight_class = 400
            self.typo_ascender: Optional[int] = None
            self.typo_descender: Optional[int] = None
            self.cap_height: Optional[int] = None
            self.x_height: Optional[int] = None
            return
        (self._os2_version,) = self._unpack(">H", "OS/2", _OS2_VERSION)
        (self.us_weight_class,) = self._unpack(">H", "OS/2", _OS2_WEIGHT_CLASS)
        (self.typo_ascender,) = self._unpack(">h", "OS/2", _OS2_TYPO_ASCENDER)
        (self.typo_descender,) = self._unpack(">h", "OS/2", _OS2_TYPO_DESCENDER)
        if self._os2_version >= 2:
            (self.cap_height,) = self._unpack(">h", "OS/2", _OS2_CAP_HEIGHT)
            (self.x_height,) = self._unpack(">h", "OS/2", _OS2_X_HEIGHT)
        else:
            self.cap_height = None
            self.x_height = None

    # ------------------------------------------------------------------
    # Glyph access
    # ------------------------------------------------------------------

    def glyph_id(self, char: str) -> Optional[int]:
        """The original glyph ID for ``char`` (None when the font lacks it)."""
        return self.cmap.get(ord(char))

    def glyph_advance(self, gid: int) -> int:
        """The advance width of ``gid`` in font units."""
        return self.advances[gid]

    def glyph_data(self, gid: int) -> bytes:
        """The raw ``glyf`` bytes of ``gid`` (empty for empty glyphs)."""
        start = self._loca_offsets[gid]
        end = self._loca_offsets[gid + 1]
        base, _length = self._table("glyf")
        return self._data[base + start : base + end]

    def is_composite(self, gid: int) -> bool:
        """True when ``gid`` is a composite glyph (negative contour count)."""
        data = self.glyph_data(gid)
        return len(data) >= 10 and struct.unpack_from(">h", data)[0] < 0

    def composite_components(self, gid: int) -> List[int]:
        """The component glyph IDs of a composite glyph (empty for simple)."""
        data = self.glyph_data(gid)
        components: List[int] = []
        if not self.is_composite(gid):
            return components
        pos = 10
        while pos + 4 <= len(data):
            flags, index = struct.unpack_from(">HH", data, pos)
            pos += 4
            components.append(index)
            pos += 4 if flags & _GLYF_ARG_1_AND_2_ARE_WORDS else 2
            if flags & _GLYF_WE_HAVE_A_SCALE:
                pos += 2
            if flags & _GLYF_WE_HAVE_AN_X_AND_Y_SCALE:
                pos += 4
            if flags & _GLYF_WE_HAVE_A_TWO_BY_TWO:
                pos += 8
            if not flags & _GLYF_MORE_COMPONENTS:
                if flags & _GLYF_WE_HAVE_INSTRUCTIONS:
                    (instruction_length,) = struct.unpack_from(">H", data, pos)
                    pos += 2 + instruction_length
                break
        return components


# ---------------------------------------------------------------------------
# Subsetting
# ---------------------------------------------------------------------------


def _table_checksum(data: bytes) -> int:
    """Sum of all uint32 words of ``data``, zero-padded to a word boundary."""
    padded = data + b"\x00" * (-len(data) % 4)
    words = struct.unpack(">%dI" % (len(padded) // 4), padded)
    return sum(words) & 0xFFFFFFFF


def _head_checksum(data: bytes) -> int:
    """Table checksum of ``head`` with the checkSumAdjustment field zeroed."""
    buffer = bytearray(data)
    buffer[_HEAD_CHECKSUM_ADJUSTMENT : _HEAD_CHECKSUM_ADJUSTMENT + 4] = b"\x00" * 4
    return _table_checksum(bytes(buffer))


def _assemble_sfnt(tables: Sequence[Tuple[str, bytes]]) -> bytes:
    """Assemble tables into a complete sfnt with correct per-table checksums.

    Table checksums are computed with ``head``'s checkSumAdjustment field
    treated as zero; the field is then set so that the sum of all uint32
    words of the finished file equals ``0xB1B0AFBA``.
    """
    num_tables = len(tables)
    entry_selector = num_tables.bit_length() - 1
    search_range = 16 * (1 << entry_selector)
    range_shift = num_tables * 16 - search_range
    body = bytearray(
        _SFNT_VERSION.pack(0x00010000, num_tables, search_range, entry_selector, range_shift)
    )
    body += b"\x00" * (16 * num_tables)

    directory: List[Tuple[bytes, int, int, int]] = []
    offset = len(body)
    for tag, data in tables:
        tag_bytes = tag.encode("ascii")
        if offset % 4:
            body += b"\x00" * (4 - offset % 4)
            offset = len(body)
        checksum = _head_checksum(data) if tag_bytes == b"head" else _table_checksum(data)
        directory.append((tag_bytes, checksum, offset, len(data)))
        body += data
        offset = len(body)
    for index, (tag, checksum, table_offset, length) in enumerate(directory):
        _TABLE_RECORD.pack_into(body, 12 + index * 16, tag, checksum, table_offset, length)

    body += b"\x00" * (-len(body) % 4)  # whole-file sum is computed zero-padded
    words = struct.unpack(">%dI" % (len(body) // 4), bytes(body))
    adjustment = (_WHOLE_FONT_CHECKSUM - sum(words)) & 0xFFFFFFFF
    head_offset = next(entry[2] for entry in directory if entry[0] == b"head")
    struct.pack_into(">I", body, head_offset + _HEAD_CHECKSUM_ADJUSTMENT, adjustment)
    return bytes(body)


class TTFSubsetter:
    """Rebuilds a TrueType font containing only the glyphs used by ``chars``.

    Glyph 0 (``.notdef``) is always included; composite glyphs pull in their
    components recursively.  The subset keeps head/hhea/maxp/hmtx/cmap
    (format 4 only)/loca (long)/glyf/OS/2/post plus a minimal ``name`` table,
    and drops all hinting and layout tables.  Composite component indices are
    rewritten to the remapped glyph IDs.
    """

    def __init__(self, ttf: TTFFont, chars: Sequence[str]) -> None:
        self.ttf = ttf
        self.chars: Set[str] = set(chars)
        self.char_gids: Dict[str, int] = {}
        self.subset_gids: Dict[str, int] = {}
        self.glyph_map: Dict[int, int] = {}
        self._ordered: List[int] = []
        self._plan()

    def _plan(self) -> None:
        for char in sorted(self.chars):
            self.char_gids[char] = self.ttf.glyph_id(char) or 0
        gids: Set[int] = {0}
        gids.update(self.char_gids.values())
        stack = list(gids)
        while stack:
            gid = stack.pop()
            if self.ttf.is_composite(gid):
                for component in self.ttf.composite_components(gid):
                    if component not in gids:
                        gids.add(component)
                        stack.append(component)
        self._ordered = sorted(gids)
        self.glyph_map = {old: new for new, old in enumerate(self._ordered)}
        self.subset_gids = {
            char: self.glyph_map[gid] for char, gid in self.char_gids.items()
        }

    @property
    def ordered_gids(self) -> List[int]:
        """The subset glyph IDs in ascending original-glyph order."""
        return list(self._ordered)

    def build(self, postscript_name: str = "SubsetTTF") -> bytes:
        """Return the complete subset TTF bytes."""
        ttf = self.ttf
        glyphs = [ttf.glyph_data(gid) for gid in self._ordered]
        for index, gid in enumerate(self._ordered):
            if ttf.is_composite(gid):
                glyphs[index] = self._rewrite_composite(glyphs[index])

        glyf = b"".join(glyphs)
        loca = bytearray()
        offset = 0
        for data in glyphs:
            loca += struct.pack(">I", offset)
            offset += len(data)
        loca += struct.pack(">I", offset)

        hmtx = bytearray()
        for gid in self._ordered:
            hmtx += struct.pack(">Hh", ttf.advances[gid], ttf.left_side_bearings[gid])
        num_glyphs = len(self._ordered)

        head = bytearray(ttf.table_bytes("head"))
        struct.pack_into(">I", head, _HEAD_CHECKSUM_ADJUSTMENT, 0)
        struct.pack_into(">h", head, _HEAD_INDEX_TO_LOC_FORMAT, 1)  # long loca

        hhea = bytearray(ttf.table_bytes("hhea"))
        struct.pack_into(">H", hhea, _HHEA_NUMBER_OF_HMETRICS, num_glyphs)
        max_advance = max(ttf.advances[gid] for gid in self._ordered)
        struct.pack_into(">H", hhea, _HHEA_ADVANCE_WIDTH_MAX, max_advance)

        maxp = bytearray(ttf.table_bytes("maxp"))
        struct.pack_into(">H", maxp, _MAXP_NUM_GLYPHS, num_glyphs)

        post = struct.pack(
            ">IihhIIIII",
            0x00030000,
            ttf.italic_angle,
            ttf.underline_position,
            ttf.underline_thickness,
            ttf.is_fixed_pitch,
            0, 0, 0, 0,
        )

        tables: List[Tuple[str, bytes]] = [
            ("head", bytes(head)),
            ("hhea", bytes(hhea)),
            ("maxp", bytes(maxp)),
            ("OS/2", ttf.table_bytes("OS/2")),
            ("hmtx", bytes(hmtx)),
            ("cmap", self._build_cmap_format_4()),
            ("loca", bytes(loca)),
            ("glyf", glyf),
            ("name", self._build_name(postscript_name)),
            ("post", post),
        ]
        return _assemble_sfnt(tables)

    def _rewrite_composite(self, data: bytes) -> bytes:
        """Return ``data`` with every component glyph index remapped in place."""
        out = bytearray(data)
        pos = 10
        while pos + 4 <= len(data):
            flags = struct.unpack_from(">H", data, pos)[0]
            old_gid = struct.unpack_from(">H", data, pos + 2)[0]
            struct.pack_into(">H", out, pos + 2, self.glyph_map[old_gid])
            pos += 4
            pos += 4 if flags & _GLYF_ARG_1_AND_2_ARE_WORDS else 2
            if flags & _GLYF_WE_HAVE_A_SCALE:
                pos += 2
            if flags & _GLYF_WE_HAVE_AN_X_AND_Y_SCALE:
                pos += 4
            if flags & _GLYF_WE_HAVE_A_TWO_BY_TWO:
                pos += 8
            if not flags & _GLYF_MORE_COMPONENTS:
                if flags & _GLYF_WE_HAVE_INSTRUCTIONS:
                    (instruction_length,) = struct.unpack_from(">H", data, pos)
                    pos += 2 + instruction_length
                break
        return bytes(out)

    def _build_cmap_format_4(self) -> bytes:
        """A complete cmap table: one (3,1) encoding record plus a format-4
        subtable with one segment per consecutive run of used chars.

        Each run is mapped with ``idDelta`` and an empty ``glyphIdArray``
        (``idRangeOffset`` 0), so no glyph array is emitted.
        """
        segments: List[Tuple[int, int, int, int]] = []
        for char, gid in sorted(self.subset_gids.items()):
            code = ord(char)
            if segments and code == segments[-1][1] + 1 and gid == segments[-1][3] + 1:
                start, _end, start_gid, _end_gid = segments[-1]
                segments[-1] = (start, code, start_gid, gid)
            else:
                segments.append((code, code, gid, gid))
        end_codes = [segment[1] for segment in segments] + [0xFFFF]
        start_codes = [segment[0] for segment in segments] + [0xFFFF]
        deltas = [((segment[2] - segment[0]) & 0xFFFF) for segment in segments] + [1]
        range_offsets = [0] * (len(segments) + 1)
        seg_count = len(segments) + 1
        entry_selector = seg_count.bit_length() - 1
        search_range = 2 * (1 << entry_selector)
        length = 16 + 8 * seg_count
        buffer = bytearray(
            struct.pack(
                ">6H", 4, length, 0, seg_count * 2, search_range, entry_selector
            )
        )
        buffer += struct.pack(">H", seg_count * 2 - search_range)  # rangeShift
        buffer += struct.pack(">%dH" % seg_count, *end_codes)
        buffer += struct.pack(">H", 0)  # reservedPad
        buffer += struct.pack(">%dH" % seg_count, *start_codes)
        buffer += struct.pack(">%dH" % seg_count, *deltas)
        buffer += struct.pack(">%dH" % seg_count, *range_offsets)
        return (
            struct.pack(">HH", 0, 1)
            + struct.pack(">HHI", 3, 1, 12)
            + bytes(buffer)
        )

    @staticmethod
    def _build_name(postscript_name: str) -> bytes:
        """A minimal format-0 name table with one PostScript-name record."""
        encoded = postscript_name.encode("ascii", "replace")
        record = struct.pack(">6H", 3, 1, 0x0409, 6, len(encoded), 0)
        return struct.pack(">3H", 0, 1, 18) + record + encoded


def _subset_tag(face_name: str) -> str:
    """Six uppercase letters derived from ``face_name`` (deterministic)."""
    digest = hashlib.md5(face_name.encode("ascii")).digest()
    return "".join(_TAG_ALPHABET[byte % 16] for byte in digest[:6])


# ---------------------------------------------------------------------------
# Registry and emission
# ---------------------------------------------------------------------------


class FontEntry:
    """One registered font for one resource name within one document.

    Collects used characters during content generation and, once
    :meth:`generate_subset` has run, exposes the subset TTF bytes plus the
    dictionaries/streams of the Type0/CIDFontType2 chain.
    """

    def __init__(
        self,
        resource_name: str,
        base_font: str,
        *,
        face_name: str,
        italic: bool = False,
        path: Optional[Path] = None,
        data: Optional[bytes] = None,
        source: Optional[str] = None,
    ) -> None:
        self.resource_name = resource_name
        self.base_font = base_font
        self.face_name = face_name
        self.italic = italic
        self._path = path
        self._data = data
        self._source = source or (str(path) if path is not None else "<bytes>")
        self.chars: Set[str] = set()
        self.subset_name: Optional[str] = None
        self.subset_bytes: Optional[bytes] = None
        self._subsetter: Optional[TTFSubsetter] = None
        self._ttf: Optional[TTFFont] = None

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def add_chars(self, text: str) -> None:
        """Record every character of ``text`` as used with this font."""
        self.chars.update(text)

    @property
    def ttf(self) -> TTFFont:
        """The parsed source font (loaded lazily from path or bytes)."""
        if self._ttf is None:
            if self._data is not None:
                self._ttf = TTFFont(self._data, self._source)
            elif self._path is not None:
                self._ttf = TTFFont.from_file(self._path)
            else:
                raise ValueError(f"font {self.resource_name!r} has no data source")
        return self._ttf

    # ------------------------------------------------------------------
    # Subset generation
    # ------------------------------------------------------------------

    def generate_subset(self) -> None:
        """Build the glyph subset once all used characters are known.

        Phase 6: the expensive ``TTFSubsetter.build`` (sfnt assembly plus
        per-table checksums) is served from the process-level subset cache
        when this font path + character set was subset before; the subset
        planning (glyph maps, metrics) always runs so the cached bytes can
        never diverge from a freshly built subset.
        """
        if self.subset_bytes is not None:
            return
        subsetter = TTFSubsetter(self.ttf, sorted(self.chars))
        self._subsetter = subsetter
        self.subset_name = _subset_tag(self.face_name) + "+" + self.face_name
        postscript_name = self.subset_name.replace("+", "")
        key = None
        cached: Optional[bytes] = None
        if self._path is not None:
            key = (str(self._path), frozenset(self.chars))
            cached = _SUBSET_BYTES_CACHE.get(key)
            if cached is not None:
                _SUBSET_BYTES_CACHE.move_to_end(key)
        if cached is None:
            cached = subsetter.build(postscript_name)
            if key is not None:
                _SUBSET_BYTES_CACHE[key] = cached
                if len(_SUBSET_BYTES_CACHE) > _SUBSET_BYTES_CACHE_MAX:
                    _SUBSET_BYTES_CACHE.popitem(last=False)
        self.subset_bytes = cached

    def require_subset(self) -> TTFSubsetter:
        """The subsetter; raises when :meth:`generate_subset` was not called."""
        if self._subsetter is None:
            raise ValueError(
                f"font {self.resource_name!r}: subsets must be generated before emission"
            )
        return self._subsetter

    # ------------------------------------------------------------------
    # Metrics (1000-unit space, per the PDF FontDescriptor convention)
    # ------------------------------------------------------------------

    def _scale(self, value: int) -> int:
        return round(value * 1000 / self.ttf.units_per_em)

    @property
    def widths(self) -> List[int]:
        """Subset glyph advances in 1000-unit space, in subset glyph ID order."""
        ttf = self.ttf
        return [self._scale(ttf.advances[gid]) for gid in self.require_subset().ordered_gids]

    @property
    def default_width(self) -> int:
        """The advance of glyph 0 (``.notdef``) in 1000-unit space."""
        return self._scale(self.ttf.glyph_advance(0))

    @property
    def metrics(self) -> Dict[str, Any]:
        """FontDescriptor metrics derived from the source font."""
        ttf = self.ttf
        upem = ttf.units_per_em
        cap_height = ttf.cap_height if ttf.cap_height is not None else int(upem * 0.7)
        x_height = ttf.x_height if ttf.x_height is not None else upem // 2
        italic = self.italic or "Italic" in self.face_name or "Oblique" in self.face_name
        flags = 32  # nonsymbolic
        if "Mono" in self.face_name or self.base_font.startswith("Courier"):
            flags |= 1
        if "Serif" in self.face_name or self.base_font.startswith("Times"):
            flags |= 2
        if italic:
            flags |= 64
        return {
            "flags": flags,
            "font_bbox": [self._scale(v) for v in (ttf.x_min, ttf.y_min, ttf.x_max, ttf.y_max)],
            "italic_angle": round(ttf.italic_angle / 65536.0, 2),
            "ascent": self._scale(ttf.ascent),
            "descent": self._scale(ttf.descent),
            "cap_height": self._scale(cap_height),
            "stem_v": max(40, ttf.us_weight_class * 2 // 10),
            "x_height": self._scale(x_height),
        }

    # ------------------------------------------------------------------
    # CID chain emission (values and stream data; object ids passed in)
    # ------------------------------------------------------------------

    def type0_dict(self, cid_font_ref: ObjectId, tounicode_ref: ObjectId) -> Dict[PdfName, Any]:
        """The Type0 font dictionary."""
        return {
            N("Type"): N("Font"),
            N("Subtype"): N("Type0"),
            N("BaseFont"): N(self.subset_name or self.face_name),
            N("Encoding"): N("Identity-H"),
            N("DescendantFonts"): [cid_font_ref],
            N("ToUnicode"): tounicode_ref,
        }

    def cid_font_dict(self, descriptor_ref: ObjectId) -> Dict[PdfName, Any]:
        """The CIDFontType2 dictionary with widths and CIDToGIDMap."""
        return {
            N("Type"): N("Font"),
            N("Subtype"): N("CIDFontType2"),
            N("BaseFont"): N(self.subset_name or self.face_name),
            N("CIDSystemInfo"): {
                N("Registry"): "Adobe",
                N("Ordering"): "Identity",
                N("Supplement"): 0,
            },
            N("FontDescriptor"): descriptor_ref,
            N("DW"): self.default_width,
            N("W"): self.widths_array(),
            N("CIDToGIDMap"): N("Identity"),
        }

    def descriptor_dict(self, font_file_ref: ObjectId, cidset_ref: ObjectId) -> Dict[PdfName, Any]:
        """The FontDescriptor dictionary referencing the subset streams."""
        metrics = self.metrics
        return {
            N("Type"): N("FontDescriptor"),
            N("FontName"): N(self.subset_name or self.face_name),
            N("Flags"): metrics["flags"],
            N("FontBBox"): metrics["font_bbox"],
            N("ItalicAngle"): metrics["italic_angle"],
            N("Ascent"): metrics["ascent"],
            N("Descent"): metrics["descent"],
            N("CapHeight"): metrics["cap_height"],
            N("StemV"): metrics["stem_v"],
            N("XHeight"): metrics["x_height"],
            N("FontFile2"): font_file_ref,
            N("CIDSet"): cidset_ref,
        }

    def font_file2_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        """The FlateDecode FontFile2 stream data and stream dict."""
        if self.subset_bytes is None:
            raise ValueError(f"font {self.resource_name!r}: subset not generated yet")
        return compressed_stream(self.subset_bytes), {N("Filter"): N("FlateDecode")}

    def cidset_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        """The CIDSet stream data and stream dict (PDF/A requires it).

        One bit per subset glyph, most-significant bit first, all set since
        every glyph in the subset is present.
        """
        count = len(self.require_subset().ordered_gids)
        bits = bytearray((count + 7) // 8)
        for cid in range(count):
            bits[cid // 8] |= 1 << (7 - cid % 8)
        return bytes(bits), {}

    def tounicode_stream(self) -> Tuple[bytes, Dict[PdfName, Any]]:
        """The FlateDecode ToUnicode CMap stream data and stream dict."""
        return compressed_stream(self.build_tounicode_cmap()), {
            N("Filter"): N("FlateDecode")
        }

    def widths_array(self) -> List[Any]:
        """The flat ``/W`` array (ISO 32000-1 9.7.4.3): each CID is followed
        by either a widths array (``c [w...]``) or a ``clast w`` pair for an
        equal-width run (``c1 c2 w``)."""
        widths = self.widths
        result: List[Any] = []
        index = 0
        while index < len(widths):
            end = index
            while end + 1 < len(widths) and widths[end + 1] == widths[index]:
                end += 1
            result.append(index)
            if index == end:
                result.append([widths[index]])
            else:
                result.append(end)
                result.append(widths[index])
            index = end + 1
        return result

    def build_tounicode_cmap(self) -> bytes:
        """A ``/Adobe-Identity-UCS`` CMap mapping subset CIDs to UTF-16BE."""
        subsetter = self.require_subset()
        by_cid: Dict[int, str] = {}
        for char in sorted(self.chars):
            by_cid.setdefault(subsetter.subset_gids[char], char)
        entries = [
            b"<%04X> <%s>" % (cid, char.encode("utf-16-be").hex().upper().encode("ascii"))
            for cid, char in sorted(by_cid.items())
        ]
        lines = [
            b"/CIDInit /ProcSet findresource begin",
            b"12 dict begin",
            b"begincmap",
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            b"/CMapName /Adobe-Identity-UCS def",
            b"/CMapType 2 def",
            b"1 begincodespacerange",
            b"<0000> <FFFF>",
            b"endcodespacerange",
            b"%d beginbfchar" % len(entries),
        ]
        lines.extend(entries)
        lines.extend(
            [
                b"endbfchar",
                b"endcmap",
                b"CMapName currentdict /CMap defineresource pop",
                b"end",
                b"end",
            ]
        )
        return b"\n".join(lines) + b"\n"


class FontChain:
    """The six indirect objects of one embedded font's CID chain.

    Two lifecycle styles: reserve-now/attach-at-render via
    :meth:`reserve_thunks` (used by the flow-driven DocumentBuilder, whose
    thunks evaluate after subsets are generated) and reserve-then-attach via
    :meth:`reserve_ids` + :meth:`attach` (used by build_minimal_document).
    """

    def __init__(self, entry: FontEntry) -> None:
        self.entry = entry
        self.type0_ref: Optional[ObjectId] = None
        self.cid_ref: Optional[ObjectId] = None
        self.descriptor_ref: Optional[ObjectId] = None
        self.font_file_ref: Optional[ObjectId] = None
        self.cidset_ref: Optional[ObjectId] = None
        self.tounicode_ref: Optional[ObjectId] = None

    def reserve_thunks(self, builder: Any) -> ObjectId:
        """Reserve the six objects through ``builder``'s deferred-queue API."""
        self.type0_ref = builder._reserve_value(
            lambda: self.entry.type0_dict(self.cid_ref, self.tounicode_ref)
        )
        self.cid_ref = builder._reserve_value(
            lambda: self.entry.cid_font_dict(self.descriptor_ref)
        )
        self.descriptor_ref = builder._reserve_value(
            lambda: self.entry.descriptor_dict(self.font_file_ref, self.cidset_ref)
        )
        self.font_file_ref = builder._reserve_stream(lambda: self.entry.font_file2_stream())
        self.cidset_ref = builder._reserve_stream(lambda: self.entry.cidset_stream())
        self.tounicode_ref = builder._reserve_stream(lambda: self.entry.tounicode_stream())
        return self.type0_ref

    def reserve_ids(self, doc: Any) -> ObjectId:
        """Reserve six consecutive object IDs (type0 first)."""
        self.type0_ref = doc.reserve()
        self.cid_ref = doc.reserve()
        self.descriptor_ref = doc.reserve()
        self.font_file_ref = doc.reserve()
        self.cidset_ref = doc.reserve()
        self.tounicode_ref = doc.reserve()
        return self.type0_ref

    def attach(self, doc: Any) -> None:
        """Attach all six bodies in ascending ID order."""
        doc.set_value(
            self.type0_ref, self.entry.type0_dict(self.cid_ref, self.tounicode_ref)
        )
        doc.set_value(self.cid_ref, self.entry.cid_font_dict(self.descriptor_ref))
        doc.set_value(
            self.descriptor_ref,
            self.entry.descriptor_dict(self.font_file_ref, self.cidset_ref),
        )
        data, extra = self.entry.font_file2_stream()
        doc.set_stream(self.font_file_ref, data, extra)
        data, extra = self.entry.cidset_stream()
        doc.set_stream(self.cidset_ref, data, extra)
        data, extra = self.entry.tounicode_stream()
        doc.set_stream(self.tounicode_ref, data, extra)


class FontRegistry:
    """Per-document font bookkeeping: Liberation map, usage, subsets.

    One registry per generated PDF keeps character usage isolated between
    documents.  In embed mode every standard font resolves to a Liberation
    face; :meth:`generate_subsets` runs once at render time when all content
    characters are known.
    """

    def __init__(self, *, embed: bool = False) -> None:
        self.embed = embed
        self._entries: Dict[str, FontEntry] = {}

    def entry(self, resource_name: str, base_font: str) -> FontEntry:
        """Return the entry for a standard font, resolving Liberation faces."""
        existing = self._entries.get(resource_name)
        if existing is not None:
            return existing
        if base_font not in STANDARD_TO_LIBERATION:
            raise ValueError(
                f"standard font {base_font!r} has no Liberation face; "
                f"register a custom TTF with register_ttf() instead"
            )
        face = STANDARD_TO_LIBERATION[base_font]
        path = LIBERATION_FONT_PATHS.get(face)
        if path is None or not path.is_file():
            raise FileNotFoundError(
                f"Liberation font {face} not found at {path or LIBERATION_FONT_DIR} -- "
                f"install the fonts-liberation package"
            )
        family_name = face.split("-")[0]  # "LiberationSans-Regular" -> "LiberationSans"
        is_italic = "Italic" in face or "Oblique" in face
        entry = FontEntry(
            resource_name, base_font, face_name=family_name, italic=is_italic, path=path
        )
        self._entries[resource_name] = entry
        return entry

    def register_ttf(
        self,
        resource_name: str,
        path: Union[str, Path],
        base_font: Optional[str] = None,
    ) -> FontEntry:
        """Register a custom TTF file under ``resource_name``."""
        path = Path(path)
        entry = FontEntry(
            resource_name,
            base_font or path.stem,
            face_name=path.stem,
            path=path,
        )
        self._entries[resource_name] = entry
        return entry

    def register_ttf_bytes(
        self, resource_name: str, data: bytes, base_font: str
    ) -> FontEntry:
        """Register an in-memory TTF under ``resource_name``."""
        entry = FontEntry(
            resource_name, base_font, face_name=base_font, data=data, source="<bytes>"
        )
        self._entries[resource_name] = entry
        return entry

    def record_chars(self, resource_name: str, text: str) -> None:
        """Record every character of ``text`` against the named font entry."""
        entry = self._entries.get(resource_name)
        if entry is not None:
            entry.add_chars(text)

    def font_is_cid(self, resource_name: str) -> bool:
        """True when embed mode is on and ``resource_name`` is registered."""
        return self.embed and resource_name in self._entries

    def cid_for(self, resource_name: str, char: str) -> int:
        """The subset CID (glyph ID) used in content text for ``char``.

        Only valid after :meth:`generate_subsets`; characters the font lacks
        map to glyph 0 (``.notdef``).
        """
        entry = self._entries[resource_name]
        return entry.require_subset().subset_gids.get(char, 0)

    def entries(self) -> Sequence[FontEntry]:
        """All registered entries in registration order."""
        return list(self._entries.values())

    def generate_subsets(self) -> None:
        """Build every entry's glyph subset (no-op outside embed mode)."""
        if not self.embed:
            return
        for entry in self._entries.values():
            entry.generate_subset()

    def subset_ttf_bytes(self, resource_name: str) -> bytes:
        """The subset TTF bytes of ``resource_name`` (after generate_subsets)."""
        return self._entries[resource_name].subset_bytes

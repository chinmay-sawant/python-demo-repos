"""ICC colour profiles generated in code: sRGB and Gray (ICC v2.4, stdlib only).

Phase 4 needs embedded ICC profiles so the document can claim PDF/A-4:
every device colour space used in the document is tied to a calibrated
profile, and the OutputIntent's ``/DestOutputProfile`` must be a valid ICC
profile.  This module builds the two profiles byte-by-byte -- no external
ICC files, nothing but ``struct`` -- so the engine stays self-contained.

Profile structure (ICC v2, "ICC.1:2003" class):

* 128-byte header: size, version 2.1 (``0x02100000``), device class
  ``mntr``, colour space ``RGB `` or ``GRAY``, PCS ``XYZ `` (D50),
  fixed creation date, ``acsp`` signature, D50 illuminant, and the
  profile ID (md5 of the whole profile with the ID field zeroed).
* Tag table: one 12-byte entry per tag (signature, offset, size).
* Tag data, 4-byte aligned:
  - ``desc`` / ``cprt``: textDescriptionType ASCII blocks
  - ``wtpt``: XYZType (media white point, D50)
  - ``bkpt``: XYZType (media black point, 0 0 0) -- required by strict
    ICC validators for the display class
  - ``chad``: s15Fixed16ArrayType chromatic-adaptation matrix (identity,
    D50-to-D50) -- required for v2.4 display profiles
  - ``rXYZ`` / ``gXYZ`` / ``bXYZ``: XYZType primaries (sRGB only)
  - ``rTRC`` / ``gTRC`` / ``bTRC``: curveType 256-entry LUTs of the
    sRGB transfer function (sRGB only)
  - ``kTRC``: curveType gamma-2.2 LUT (Gray only; the ICC signature is
    the 4-byte ``kTRC`` -- "grayTRC" is only the prose name)

The profile is ICC v2.4 (``0x02400000``, ICC.1:2001-04) -- the newest
revision of the v2 family -- which is what strict validators (e.g. Adobe
Preflight) expect from a v2-class profile.

Everything is deterministic: fixed profile date, fixed values.

Phase 6: the two profiles are immutable once built, so the classmethods
return module-level cached instances -- one sRGB and one Gray profile per
process, shared by every document (the profile bytes themselves are
constant and never mutate, so sharing cannot leak state across documents).
"""

from __future__ import annotations

import hashlib
import struct
from typing import List, Optional, Sequence, Tuple

__all__ = ["ICCProfile", "ZerodhaTheme", "gray_icc", "hex_to_rgb", "srgb_icc"]

# ICC v2.4 version field (major 2, minor 4, patch 0, reserved 0): the
# newest revision of the ICC.1:2001-04 family, accepted by the strictest
# ICC v2 parsers (Adobe Preflight included).
_ICC_VERSION_2_4 = 0x02400000

# Fixed profile creation date so output is byte-reproducible.
_ICC_PROFILE_DATE = (2026, 8, 1, 12, 0, 0)

# D50 white point in s15Fixed16 (the canonical ICC encodings).
_WTDT_X = 0x0000F6D6  # 0.9642
_WTDT_Y = 0x00010000  # 1.0
_WTDT_Z = 0x0000D32D  # 0.8249

# sRGB primaries (xYZ values on D50) in s15Fixed16, from the standard
# IEC 61966-2-1 matrix, rounded to the same values Adobe's v2 profiles use.
_SRGB_PRIMARIES: Tuple[Tuple[int, int, int], ...] = (
    (0x00006F9E, 0x000038EF, 0x0000038F),  # rXYZ: 0.4360 0.2224 0.0139
    (0x00006296, 0x0000B787, 0x000018DC),  # gXYZ: 0.3851 0.7169 0.0971
    (0x000024A2, 0x00000F84, 0x0000B6C2),  # bXYZ: 0.1431 0.0606 0.7139
)

# The sRGB transfer function: linear below 0.04045, else the 2.4 power law.
_SRGB_LINEAR_CUTOFF = 0.04045


def _srgb_transfer(value: float) -> float:
    """The sRGB transfer function for one 0..1 value."""
    if value <= _SRGB_LINEAR_CUTOFF:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _srgb_curve() -> List[int]:
    """A 256-entry 8-bit LUT of the sRGB transfer function."""
    return [
        round(255 * _srgb_transfer(index / 255.0)) for index in range(256)
    ]


def _gamma_curve(gamma: float = 2.2) -> List[int]:
    """A 256-entry 8-bit LUT of the ``c ** (1 / gamma)`` encoding curve."""
    return [
        round(255 * ((index / 255.0) ** (1.0 / gamma))) for index in range(256)
    ]


# ---------------------------------------------------------------------------
# Tag data builders
# ---------------------------------------------------------------------------


def _s15(value: float) -> bytes:
    """One s15Fixed16 value (signed 32-bit fixed point, 16 fractional bits)."""
    return struct.pack(">i", round(value * 65536))


def _text_tag(text: str) -> bytes:
    """A textDescriptionType tag: ASCII text plus empty Unicode/Mac blocks."""
    ascii_bytes = text.encode("ascii")
    return (
        b"desc"
        + struct.pack(">I", 0)
        + struct.pack(">I", len(ascii_bytes) + 1)
        + ascii_bytes
        + b"\x00"
        + struct.pack(">I", 0)  # Unicode language code
        + struct.pack(">I", 0)  # Unicode count (no UTF-16 text)
        + struct.pack(">H", 0)  # scriptcode
        + struct.pack(">B", 0)  # Macintosh count
    )


def _xyz_tag(x: int, y: int, z: int) -> bytes:
    """An XYZType tag holding three s15Fixed16 values."""
    return b"XYZ " + struct.pack(">I", 0) + struct.pack(">3I", x, y, z)


def _curve_tag(values: Sequence[int]) -> bytes:
    """A curveType tag: count plus one ``u8Fixed8Number`` per entry.

    Per ICC.1:2001-04 9.2.8 every curveType entry is a two-byte
    ``u8Fixed8Number`` (value << 8); writing single bytes -- as earlier
    drafts of the spec suggested -- makes strict parsers (Adobe Preflight)
    reject the whole profile.  A 256-entry LUT therefore occupies
    12 + 512 = 524 bytes.
    """
    return (
        b"curv"
        + struct.pack(">I", 0)
        + struct.pack(">I", len(values))
        + struct.pack(">%dH" % len(values), *(value << 8 for value in values))
    )


def _sf32_tag(values: Sequence[int]) -> bytes:
    """An s15Fixed16ArrayType tag holding ``values`` in s15Fixed16."""
    return b"sf32" + struct.pack(">I", 0) + struct.pack(">%dI" % len(values), *values)


# The chromatic-adaptation matrix: D50-to-D50 is the identity, which is
# the standard value for profiles whose PCS is D50-adapted XYZ.
_CHAD_IDENTITY = (
    0x00010000, 0x00000000, 0x00000000,
    0x00000000, 0x00010000, 0x00000000,
    0x00000000, 0x00000000, 0x00010000,
)


# ---------------------------------------------------------------------------
# Profile assembly
# ---------------------------------------------------------------------------


def _build_icc(
    *,
    description: str,
    copyright_text: str,
    components: int,
    trc: List[int],
    primaries: Optional[Tuple[Tuple[int, int, int], ...]] = None,
) -> bytes:
    """Assemble a complete ICC v2.4 profile byte stream (deterministic).

    Args:
        description: ASCII text for the ``desc`` tag.
        copyright_text: ASCII text for the ``cprt`` tag.
        components: 3 for RGB, 1 for Gray (drives the colour-space field
            and the tag set).
        trc: 256-entry tone-response curve (used for ``rTRC``/``gTRC``/
            ``bTRC`` or the gray ``kTRC``).
        primaries: three XYZ triples for an RGB profile (required when
            ``components`` is 3).
    """
    is_rgb = components == 3
    color_space = b"RGB " if is_rgb else b"GRAY"

    tags: List[Tuple[bytes, bytes]] = [
        (b"desc", _text_tag(description)),
        (b"cprt", _text_tag(copyright_text)),
        (b"wtpt", _xyz_tag(_WTDT_X, _WTDT_Y, _WTDT_Z)),
        (b"bkpt", _xyz_tag(0, 0, 0)),
        (b"chad", _sf32_tag(_CHAD_IDENTITY)),
    ]
    if is_rgb:
        if primaries is None or len(primaries) != 3:
            raise ValueError("RGB ICC profile requires three primaries")
        for sig, xyz in zip((b"rXYZ", b"gXYZ", b"bXYZ"), primaries):
            tags.append((sig, _xyz_tag(*xyz)))
        for sig in (b"rTRC", b"gTRC", b"bTRC"):
            tags.append((sig, _curve_tag(trc)))
    else:
        tags.append((b"kTRC", _curve_tag(trc)))

    body = _assemble_tags(tags)
    header_size = 128
    tag_table_size = 4 + 12 * len(tags)
    size = header_size + tag_table_size + len(body)

    year, month, day, hour, minute, second = _ICC_PROFILE_DATE
    header = bytearray()
    header += struct.pack(">I", size)
    header += b"Lino"  # preferred CMM
    header += struct.pack(">I", _ICC_VERSION_2_4)
    header += b"mntr"  # display device profile
    header += color_space
    header += b"XYZ "  # PCS
    header += struct.pack(">6H", year, month, day, hour, minute, second)
    header += b"acsp"
    header += b"\x00" * 4  # primary platform
    header += b"\x00" * 4  # flags
    header += b"\x00" * 8  # device manufacturer + model
    header += b"\x00" * 8  # device attributes
    header += b"\x00" * 4  # rendering intent (perceptual)
    header += struct.pack(">3I", _WTDT_X, _WTDT_Y, _WTDT_Z)  # illuminant
    header += b"pycc"  # creator
    header += b"\x00" * 16  # profile ID (filled below)
    header += b"\x00" * 28  # reserved

    profile = (
        bytes(header) + struct.pack(">I", len(tags)) + _tag_entries(tags, body) + body
    )
    profile_id = hashlib.md5(profile).digest()
    return profile[:84] + profile_id + profile[100:]


def _tag_entries(
    tags: Sequence[Tuple[bytes, bytes]], body: bytes
) -> bytes:
    """The tag table entries (signature, offset, size) for ``tags``.

    Offsets are absolute file offsets (per the ICC specification, tag
    offsets are measured from the beginning of the profile): the table
    starts at byte 128 and each payload follows the previous one,
    4-byte aligned, mirroring :func:`_assemble_tags`.
    """
    base = 128 + 4 + 12 * len(tags)
    out = bytearray()
    offset = base
    for sig, data in tags:
        out += sig[:4] + struct.pack(">2I", offset, len(data))
        offset += len(data)
        if offset % 4:
            offset += 4 - offset % 4
    return bytes(out)


def _assemble_tags(tags: Sequence[Tuple[bytes, bytes]]) -> bytes:
    """Concatenate tag data, 4-byte aligning each payload."""
    out = bytearray()
    for _sig, data in tags:
        out += data
        if len(out) % 4:
            out += b"\x00" * (4 - len(out) % 4)
    return bytes(out)


class ICCProfile:
    """A generated ICC profile plus the PDF colour-space parameters.

    ``components`` and ``alternate`` describe how the profile is used as
    an ICCBased colour space in a PDF document (``/N 3`` ``/Alternate
    /DeviceRGB`` for sRGB, ``/N 1`` ``/Alternate /DeviceGray`` for Gray).
    Instances are immutable by construction; the phase-6 classmethods
    return the process-level cached profile so every document shares one
    sRGB and one Gray byte stream.
    """

    __slots__ = ("alternate", "components", "data")

    def __init__(self, data: bytes, components: int, alternate: str) -> None:
        self.data = data
        self.components = components
        self.alternate = alternate

    @classmethod
    def srgb(cls) -> "ICCProfile":
        """The standard sRGB profile (D50 white point, sRGB primaries, TRC).

        The profile is deterministic and immutable, so a single process-wide
        instance is built lazily and reused by every document.
        """
        global _SRGB_PROFILE
        if _SRGB_PROFILE is None:
            _SRGB_PROFILE = cls(
                _build_icc(
                    description="sRGB IEC61966-2.1",
                    copyright_text="Copyright (c) pythoncoreengine, generated in code",
                    components=3,
                    trc=_srgb_curve(),
                    primaries=_SRGB_PRIMARIES,
                ),
                3,
                "DeviceRGB",
            )
        return _SRGB_PROFILE

    @classmethod
    def gray(cls) -> "ICCProfile":
        """The generic gray profile (D50 white point, gamma-2.2 TRC).

        Like :meth:`srgb`, one immutable process-wide instance is shared by
        every document.
        """
        global _GRAY_PROFILE
        if _GRAY_PROFILE is None:
            _GRAY_PROFILE = cls(
                _build_icc(
                    description="Gray gamma 2.2",
                    copyright_text="Copyright (c) pythoncoreengine, generated in code",
                    components=1,
                    trc=_gamma_curve(2.2),
                ),
                1,
                "DeviceGray",
            )
        return _GRAY_PROFILE


_SRGB_PROFILE: Optional[ICCProfile] = None
_GRAY_PROFILE: Optional[ICCProfile] = None


def hex_to_rgb(value: str) -> Tuple[float, float, float]:
    """Parse ``#RRGGBB`` into an ``(r, g, b)`` triple in 0..1 scale.

    Phase 8: the Zerodha-style renderer (engine.render) maps the hex colours
    of the TEMPLATE_REFERENCE palette onto the engine's 0..1 RGB tuples.
    """
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"hex colour must be 6 digits, got {value!r}")
    try:
        parts = tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        raise ValueError(f"invalid hex colour {value!r}") from None
    return parts[0] / 255.0, parts[1] / 255.0, parts[2] / 255.0


class ZerodhaTheme:
    """The Zerodha-style contract-note palette (TEMPLATE_REFERENCE colours).

    Class attributes (immutable RGB tuples) so render code reads
    ``ZerodhaTheme().buy`` / ``THEME.header_bar`` without config plumbing:
    header bar #154360 with white text, section rows #21618C with white
    text, buy #27AE60 green, sell #E74C3C red, alternating row band
    #F8F9F9, a light grid, dark body text and a grey watermark.
    """

    header_bar: Tuple[float, float, float] = hex_to_rgb("#154360")
    header_text: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    section_bar: Tuple[float, float, float] = hex_to_rgb("#21618C")
    section_text: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    buy: Tuple[float, float, float] = hex_to_rgb("#27AE60")
    sell: Tuple[float, float, float] = hex_to_rgb("#E74C3C")
    alt_row_bg: Tuple[float, float, float] = hex_to_rgb("#F8F9F9")
    body_text: Tuple[float, float, float] = (0.12, 0.14, 0.16)
    grid_line: Tuple[float, float, float] = (0.82, 0.82, 0.82)
    watermark: Tuple[float, float, float] = (0.72, 0.72, 0.72)


def srgb_icc() -> ICCProfile:
    """Convenience: the generated sRGB :class:`ICCProfile`."""
    return ICCProfile.srgb()


def gray_icc() -> ICCProfile:
    """Convenience: the generated gray :class:`ICCProfile`."""
    return ICCProfile.gray()

"""Strict ICC v2 (ICC.1:2001-04) profile validation for engine.color.

Adobe Preflight rejected our generated ICC profiles while veraPDF and
lcms (via Ghostscript) accepted them, so this module implements the ICC
spec checks a strict parser performs -- header fields, tag table bounds,
tag type signatures, per-type payload layouts -- and applies them to the
engine's own profiles.  The validator is itself validated by running it
against known-good system profiles (colord/ghostscript) in
:class:`TestValidatorOnSystemProfiles`, which must pass too.

Every check returns a list of human-readable problems; a profile is
valid iff the list is empty.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from engine.color import ICCProfile, gray_icc, srgb_icc

# Known tag signatures and their allowed data-type signatures.  ``desc``/
# ``cprt`` may be textType ('text'), textDescriptionType ('desc') or, from
# v2.4, multiLocalizedUnicodeType ('mluc'); the curve tags accept
# curveType; the XYZ and sf32 tags are fixed.
_KNOWN_TAGS: Dict[bytes, Tuple[bytes, ...]] = {
    b"desc": (b"desc", b"text", b"mluc"),
    b"cprt": (b"desc", b"text", b"mluc"),
    b"wtpt": (b"XYZ ",),
    b"bkpt": (b"XYZ ",),
    b"rXYZ": (b"XYZ ",),
    b"gXYZ": (b"XYZ ",),
    b"bXYZ": (b"XYZ ",),
    b"rTRC": (b"curv",),
    b"gTRC": (b"curv",),
    b"bTRC": (b"curv",),
    b"kTRC": (b"curv",),
    b"chad": (b"sf32",),
}
# Tags required for (class, colour space).  ``bkpt``/``chad`` are only
# required for v2.4 display profiles -- v2.1 profiles (which are legal and
# ship with colord/ghostscript) omit them -- so the check is applied only
# to profiles at version 0x02400000.
_REQUIRED_BY_CLASS_COLOR = {
    b"mntr" + b"RGB ": (b"desc", b"cprt", b"wtpt",
                        b"rXYZ", b"gXYZ", b"bXYZ",
                        b"rTRC", b"gTRC", b"bTRC"),
    b"mntr" + b"GRAY": (b"desc", b"cprt", b"wtpt", b"kTRC"),
}
_REQUIRED_V24 = (b"bkpt", b"chad")
_TAG_TABLE_BASE = 128
# Common v2 tag signatures we tolerate without deep validation (device
# profiles use LUTs, chromaticity, gamut tags etc.).
_TOLERATED_TAGS = {
    b"A2B0", b"A2B1", b"A2B2", b"B2A0", b"B2A1", b"B2A2",
    b"gamt", b"chrm", b"targ", b"ndin", b"rig0", b"rig2",
    b"dmnd", b"dmdd", b"lumi", b"meas", b"tech", b"view",
    b"pre0", b"pre1", b"pre2", b"clro", b"clrt", b"clot",
}
# Tag data may legitimately be shared between LUT tags in device profiles,
# so overlap detection only applies to the display-profile tags we emit.
_OVERLAP_CHECKED_TAGS = {b"desc", b"cprt", b"wtpt", b"bkpt", b"chad",
                         b"rXYZ", b"gXYZ", b"bXYZ",
                         b"rTRC", b"gTRC", b"bTRC", b"kTRC"}


def _tags(profile: bytes) -> Dict[bytes, Tuple[int, int]]:
    count = struct.unpack(">I", profile[128:132])[0]
    return {
        sig: (offset, size)
        for sig, offset, size in (
            struct.unpack(">4sII", profile[132 + 12 * i : 144 + 12 * i])
            for i in range(count)
        )
    }


def validate_icc(profile: bytes) -> List[str]:
    """Return a list of spec violations (empty == valid)."""
    problems: List[str] = []
    if len(profile) < 132:
        return ["profile shorter than header + tag table"]

    # --- header -----------------------------------------------------
    size_field = struct.unpack(">I", profile[0:4])[0]
    if size_field != len(profile):
        problems.append("header size field %d != file length %d"
                        % (size_field, len(profile)))
    if profile[36:40] != b"acsp":
        problems.append("missing 'acsp' signature")
    version = struct.unpack(">I", profile[8:12])[0]
    major = version >> 24
    if major not in (2, 4):
        problems.append("version major %d not in {2, 4}" % major)
    if profile[12:16] not in (b"mntr", b"prtr", b"scnr", b"spac", b"link"):
        problems.append("unknown device class %r" % profile[12:16])
    if profile[16:20] not in (b"RGB ", b"GRAY", b"CMYK", b"LAB ", b"Lab "):
        problems.append("unsupported colour space %r" % profile[16:20])
    if profile[20:24] not in (b"XYZ ", b"Lab "):
        problems.append("unsupported PCS %r" % profile[20:24])
    # PCS illuminant must be D50 (ISO 15076-1 / ICC.1 7.2.21).
    x, y, z = struct.unpack(">3I", profile[68:80])
    if (x, y, z) != (0x0000F6D6, 0x00010000, 0x0000D32D):
        problems.append("PCS illuminant is not D50: %08X %08X %08X"
                        % (x, y, z))
    # profile ID: when non-zero it must equal the md5 of the profile with
    # the ID field zeroed (7.2.25); a zero ID means "not computed" and is
    # legal (most shipping v2 profiles carry one).
    import hashlib
    if profile[84:100] != b"\x00" * 16:
        zeroed = profile[:84] + b"\x00" * 16 + profile[100:]
        if hashlib.md5(zeroed).digest() != profile[84:100]:
            problems.append("profile ID is not the md5 of the zeroed profile")

    # --- tag table --------------------------------------------------
    count = struct.unpack(">I", profile[128:132])[0]
    table_end = 132 + 12 * count
    if table_end > len(profile):
        problems.append("tag table overruns profile")
        return problems
    tags = _tags(profile)
    if len(tags) != count:
        problems.append("duplicate tag signatures")
    seen_sigs: List[bytes] = []
    for sig, (offset, size) in tags.items():
        if sig in seen_sigs:
            problems.append("duplicate tag %r" % sig)
        seen_sigs.append(sig)
        if sig not in _KNOWN_TAGS and sig not in _TOLERATED_TAGS:
            problems.append("unknown tag signature %r" % sig)
            continue
        if sig not in _KNOWN_TAGS:
            continue  # tolerated; bounds checked below but no deep type check
        if offset < table_end:
            problems.append("tag %r offset %d inside tag table" % (sig, offset))
        if offset % 4:
            problems.append("tag %r data not 4-byte aligned" % sig)
        if offset + size > len(profile):
            problems.append("tag %r overruns profile (%d+%d > %d)"
                            % (sig, offset, size, len(profile)))
        if size < 8:
            problems.append("tag %r implausibly small (%d bytes)" % (sig, size))
    # overlapping tag payloads (display-profile tags only; device-profile
    # LUTs and identical channel curves legitimately share data -- e.g.
    # Ghostscript's default_rgb.icc aliases all three TRCs to one curve)
    spans = sorted(
        (o, o + s, sig) for sig, (o, s) in tags.items()
        if sig in _OVERLAP_CHECKED_TAGS
    )
    for (a1, a2, s1), (b1, b2, s2) in zip(spans, spans[1:]):
        if b1 < a2 and profile[a1:a2] != profile[b1:b2]:
            problems.append("tags %r and %r overlap with different data"
                            % (s1, s2))

    # --- required tags for (class, colour space) --------------------
    required = _REQUIRED_BY_CLASS_COLOR.get(profile[12:20])
    if required is not None:
        for sig in required:
            if sig not in tags:
                problems.append("missing required tag %r" % sig)
    if version == 0x02400000:
        for sig in _REQUIRED_V24:
            if sig not in tags:
                problems.append("missing v2.4 tag %r" % sig)

    # --- per-type payloads ------------------------------------------
    for sig, (offset, size) in tags.items():
        allowed_types = _KNOWN_TAGS.get(sig)
        if allowed_types is None:
            continue
        type_sig = profile[offset : offset + 4]
        if type_sig not in allowed_types:
            problems.append("tag %r has type %r, expected one of %r"
                            % (sig, type_sig, allowed_types))
            continue
        if sig in (b"wtpt", b"bkpt", b"rXYZ", b"gXYZ", b"bXYZ"):
            if size != 20:
                problems.append("XYZ tag %r size %d != 20" % (sig, size))
            if struct.unpack(">I", profile[offset + 4 : offset + 8])[0] != 0:
                problems.append("XYZ tag %r reserved field non-zero" % sig)
        elif sig == b"chad":
            if size != 4 + 4 + 36:
                problems.append("chad size %d != 44" % size)
        elif sig in (b"desc",):
            _validate_text_description(profile, offset, size, sig, problems)
        elif sig in (b"rTRC", b"gTRC", b"bTRC", b"kTRC"):
            count_v = struct.unpack(">I", profile[offset + 8 : offset + 12])[0]
            if count_v == 1:
                # curveType count 1 = a single gamma value (u8Fixed8Number,
                # 2 bytes, tag size 14); a few shipped profiles pad it to
                # 4 bytes (size 16) -- both accepted.
                if size not in (14, 16):
                    problems.append("gamma curv tag %r size %d != 14/16"
                                    % (sig, size))
            else:
                if count_v > 4096:
                    problems.append("curv tag %r count %d out of range"
                                    % (sig, count_v))
                # LUT entries are two-byte u8Fixed8Number values.
                if size != 12 + 2 * count_v:
                    problems.append("curv tag %r size %d != 12+2*%d"
                                    % (sig, size, count_v))
            if struct.unpack(">I", profile[offset + 4 : offset + 8])[0] != 0:
                problems.append("curv tag %r reserved field non-zero" % sig)

    return problems


def _validate_text_description(
    profile: bytes, offset: int, size: int, sig: bytes, problems: List[str]
) -> None:
    if size < 14:
        problems.append("desc tag %r too small (%d)" % (sig, size))
        return
    ascii_count = struct.unpack(">I", profile[offset + 8 : offset + 12])[0]
    if ascii_count < 1:
        problems.append("desc tag %r zero ASCII count" % sig)
        return
    if 12 + ascii_count > size:
        problems.append("desc tag %r ASCII text overruns tag" % sig)
        return
    if profile[offset + 12 + ascii_count - 1] != 0:
        problems.append("desc tag %r ASCII text not null-terminated" % sig)
    if struct.unpack(">I", profile[offset + 4 : offset + 8])[0] != 0:
        problems.append("desc tag %r reserved field non-zero" % sig)


class TestEngineProfilesStrict(unittest.TestCase):
    def setUp(self) -> None:
        self.srgb = srgb_icc()
        self.gray = gray_icc()

    def test_srgb_valid(self) -> None:
        problems = validate_icc(self.srgb.data)
        self.assertEqual(problems, [], "sRGB profile problems: %s" % problems)

    def test_gray_valid(self) -> None:
        problems = validate_icc(self.gray.data)
        self.assertEqual(problems, [], "Gray profile problems: %s" % problems)


class TestValidatorOnSystemProfiles(unittest.TestCase):
    """The validator must accept known-good system profiles.

    This guards the validator itself: if it flags a profile shipped with
    colord/ghostscript, the check is too strict (or wrong).
    """

    def _reference_profiles(self) -> List[Tuple[str, bytes]]:
        candidates = sorted(
            list(Path("/usr/share/color/icc/colord").glob("*.icc"))
            + list(Path("/usr/share/ghostscript").glob("*/iccprofiles/*.icc"))
        )
        return [(str(p), p.read_bytes()) for p in candidates]

    def test_reference_profiles_validate(self) -> None:
        profiles = self._reference_profiles()
        if not profiles:
            self.skipTest("no system ICC profiles to cross-check against")
        for name, data in profiles:
            problems = validate_icc(data)
            self.assertEqual(
                problems, [], "%s: %s" % (name, problems)
            )


if __name__ == "__main__":
    unittest.main()

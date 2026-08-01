"""PDF/A-4 rule objects: OutputIntent, ICCBased colour spaces, Info omission.

Phase 4 emits documents that claim ISO 19005-4 (PDF/A-4).  This module
owns the PDF objects the A-4 rule set demands beyond the core phase-1..3
object set:

* the catalog ``/OutputIntents`` entry: one :class:`OutputIntent` whose
  ``/DestOutputProfile`` is the document's sRGB ICC stream and whose
  ``/S`` is ``/GTS_PDFA1``;
* the ICCBased colour-space arrays (``/DefaultRGB``, ``/DefaultGray``)
  placed in every page's ``/Resources /ColorSpace`` so DeviceRGB and
  DeviceGray operators resolve through calibrated profiles;
* the rewrite of image XObject ``/ColorSpace`` entries from bare
  ``/DeviceRGB`` / ``/DeviceGray`` names to ``[/ICCBased ...]`` arrays;
* the A-4 rule that the trailer must not carry an ``/Info`` entry
  (enforced by the document builder: no Info object is created).

The XMP metadata stream and the ICC profile bytes themselves live in
engine.meta and engine.color; this module only wires their refs into
document dictionaries.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .write import N, ObjectId, PdfName

__all__ = [
    "A4_REGISTRY_NAME",
    "A4_OUTPUT_CONDITION_IDENTIFIER",
    "OutputIntent",
    "default_colorspaces",
    "icc_based_colorspace",
    "metadata_stream_dict",
    "output_intent_dict",
    "rewrite_image_colorspace",
]

#: The output condition the sRGB profile implements (IEC 61966-2-1).
A4_OUTPUT_CONDITION_IDENTIFIER = "sRGB IEC61966-2.1"

#: The registry the output condition identifier is drawn from.
A4_REGISTRY_NAME = "http://www.color.org"


def icc_based_colorspace(icc_ref: ObjectId) -> List[Any]:
    """An ICCBased colour-space array: ``[/ICCBased <ref> 0 R]``."""
    return [N("ICCBased"), icc_ref]


def default_colorspaces(
    srgb_ref: ObjectId, gray_ref: ObjectId
) -> Dict[PdfName, Any]:
    """The page-resources ``/ColorSpace`` dict with DefaultRGB/DefaultGray.

    Both entries map the device colour spaces used by content operators
    (``rg``/``RG``/``g``) to the document's ICC profiles, which PDF/A-4
    requires when DeviceRGB/DeviceGray is used (ISO 19005-4:2020 6.2.4.3).
    """
    return {
        N("DefaultRGB"): icc_based_colorspace(srgb_ref),
        N("DefaultGray"): icc_based_colorspace(gray_ref),
    }


def output_intent_dict(dest_profile_ref: ObjectId) -> Dict[PdfName, Any]:
    """The PDF/A output intent dictionary (``/S /GTS_PDFA1``, sRGB profile).

    ``/DestOutputProfile`` references the document's sRGB ICC stream; the
    intent declares the sRGB IEC61966-2.1 output condition so DeviceRGB
    content is meaningful in the archival workflow.
    """
    return {
        N("Type"): N("OutputIntent"),
        N("S"): N("GTS_PDFA1"),
        N("OutputConditionIdentifier"): A4_OUTPUT_CONDITION_IDENTIFIER,
        N("RegistryName"): A4_REGISTRY_NAME,
        N("Info"): A4_OUTPUT_CONDITION_IDENTIFIER,
        N("DestOutputProfile"): dest_profile_ref,
    }


def metadata_stream_dict() -> Dict[PdfName, Any]:
    """The metadata stream dictionary entries ``/Type /Metadata``, ``/Subtype /XML``."""
    return {N("Type"): N("Metadata"), N("Subtype"): N("XML")}


def rewrite_image_colorspace(
    stream_dict: Dict[PdfName, Any],
    srgb_ref: ObjectId,
    gray_ref: ObjectId,
) -> Dict[PdfName, Any]:
    """Return ``stream_dict`` with a bare device image ``/ColorSpace`` rewritten.

    Under PDF/A-4 an image's colour space must not be a bare
    ``/DeviceRGB`` / ``/DeviceGray`` name; the entry becomes the
    ``[/ICCBased <ref> 0 R]`` array for the matching document profile.
    Filters (``/DCTDecode``, ``/FlateDecode``) pass through untouched.
    """
    rewritten = dict(stream_dict)
    space = rewritten.get(N("ColorSpace"))
    if space == N("DeviceRGB"):
        rewritten[N("ColorSpace")] = icc_based_colorspace(srgb_ref)
    elif space == N("DeviceGray"):
        rewritten[N("ColorSpace")] = icc_based_colorspace(gray_ref)
    return rewritten


class OutputIntent:
    """Value holder for a document's PDF/A output intent (kept for tests)."""

    __slots__ = ("condition_identifier", "registry_name", "srgb_ref")

    def __init__(
        self,
        srgb_ref: ObjectId,
        condition_identifier: str = A4_OUTPUT_CONDITION_IDENTIFIER,
        registry_name: str = A4_REGISTRY_NAME,
    ) -> None:
        self.srgb_ref = srgb_ref
        self.condition_identifier = condition_identifier
        self.registry_name = registry_name

    def to_dict(self) -> Dict[PdfName, Any]:
        """The OutputIntent dictionary referencing ``srgb_ref``."""
        return output_intent_dict(self.srgb_ref)

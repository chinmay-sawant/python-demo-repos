"""Golden fixture generation for the test suite (dev entrypoint).

Usage from the project root::

    python3 -m engine.fixtures [output-dir]

Writes deterministic fixture PDFs (fixed /Info dates, deterministic image
bytes, hence byte-stable) under ``engine/tests/fixtures/`` by default.

Phase 1 fixture: a single-page minimal PDF.
Phase 2 fixtures: title + paragraph page, a 3x3 table, a long multi-page
table (60 rows x 5 columns) and a page embedding a synthetic PNG gradient
plus a structurally valid JPEG (dummy DCT data).
Phase 3 fixtures: an embedded-Liberation-Sans text page (multi-character
Unicode) and a Unicode + hyphen page, both with font subsetting enabled.
Phase 4 fixtures: the same three scenarios under ``mode_pdfa4=True`` --
a minimal text page, a 3x3 table and an image page -- each with the XMP
metadata stream, ICC profiles, OutputIntent and DefaultRGB/DefaultGray
resources that PDF/A-4 (ISO 19005-4) demands; they must pass
``verapdf -f 4``.
Phase 5 fixtures: six dual-mode documents (``mode_pdfa4=True`` +
``mode_pdfua2=True``) -- minimal text, heading+title, a 3x3 table, a
long multipage table, a figure page with /Alt text and a page with a
link annotation -- each with the tagged structure tree, BDC/EMC marked
content and pdfuaid XMP claim that PDF/UA-2 (ISO 14289-2) demands; they
must pass both ``verapdf -f 4`` and ``verapdf -f ua2``.

Phase 7 fixtures: the optional product features.  ``phase7_bookmarks.pdf``
is a dual-mode two-page document with a 3-level outline tree
(``/Outlines`` + ``/PageMode /UseOutlines``) whose destinations are
structure destinations (``/Sect`` elements), and ``phase7_form.pdf`` is a
dual-mode document with one text field and one checkbox (``/AcroForm``
with widget annotations, appearance streams and ``/Form`` structure
enclosures); both must pass ``verapdf -f 4`` and ``verapdf -f ua2``.
The plain-PDF-2.0 variants (``phase7_bookmarks_nocomply.pdf`` /
``phase7_form_nocomply.pdf``) exercise the same features without any
compliance claim (page destinations, no structure, /Info present) and are
not part of the veraPDF gate.

Phase 7.4 fixtures: ``phase7_signed.pdf`` is a dual-mode document with a
digital signature field whose placeholder was signed with a seeded
2048-bit RSA key (engine.crypto) at a fixed signing time -- byte-stable
across runs -- and ``phase7_signed_nocomply.pdf`` is the plain PDF 2.0
variant.  The signed dual-mode fixture must still pass both ``verapdf -f
4`` and ``verapdf -f ua2`` (the byte-range splice changes no offsets).

Phase 7.5 fixtures: ``phase7_encrypted.pdf`` (AES-128, revision 4) and
``phase7_encrypted_256.pdf`` (AES-256, revision 6) are plain-PDF-2.0
documents encrypted with the /Standard security handler (engine.encrypt)
using a seeded EncryptSpec, so the salts, file key and IVs -- and thus
the fixture bytes and md5 -- are deterministic.  They open with the
user password ``fixture-password`` and are not part of the veraPDF gate
(A-4 forbids encryption; the builder refuses the combination).
"""

from __future__ import annotations

import datetime
import struct
import sys
import zlib
from pathlib import Path
from typing import Dict

from . import generate_minimal_pdf
from .crypto import generate_rsa_key
from .doc import DocumentBuilder
from .encrypt import EncryptSpec
from .image import _png_chunk
from .layout import PageMargins, TableLayout
from .signature import sign_pdf

__all__ = ["generate_fixtures"]

DEFAULT_OUTPUT_DIR = Path(__file__).parent / "tests" / "fixtures"

# Fixed timestamp so generated fixtures are byte-for-byte reproducible.
_FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_HEADING = "Quarterly Report - Q3 FY26"
_PARAGRAPH = (
    "This is a paragraph produced by the pure-Python PDF engine. "
    "Text is positioned with an explicit font size and colour, and the "
    "layout layer wraps words against the available content width using "
    "the built-in Helvetica width table. "
) * 6


def _gradient_png(width: int = 64, height: int = 64) -> bytes:
    """A deterministic 64x64 RGB gradient PNG (filter type 0 everywhere)."""
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            pixels.extend(
                [x * 255 // (width - 1), y * 255 // (height - 1), ((x + y) * 255 // 126) & 0xFF]
            )
    ihdr = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(pixels)))
        + _png_chunk(b"IEND", b"")
    )


def _synthetic_jpeg(width: int = 16, height: int = 16) -> bytes:
    """A structurally parseable baseline JPEG (SOF0) with dummy DCT data."""
    sof = bytes([8]) + struct.pack(">HH", height, width) + bytes([3])
    sos = bytes([8, 2]) + bytes([1, 1, 0, 0, 63, 0])
    return (
        b"\xff\xd8"
        + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
        + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
        + b"\x00\x01"
        + b"\xff\xd9"
    )


def _title_paragraph_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED)
    flow = builder.flow()
    flow.text(_HEADING, size=18, color=(0.15, 0.25, 0.45))
    flow.paragraph(_PARAGRAPH, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _table_3x3_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED)
    flow = builder.flow()
    flow.text("Three by three table", size=16, color=(0.1, 0.1, 0.1))
    flow.table(
        TableLayout(
            col_widths=[120, 140, 140],
            header=["Metric", "Q2", "Q3"],
            rows=[
                ["Revenue", "1,240,000", "1,510,000"],
                ["Expenses", "980,000", "1,010,000"],
                ["Profit", "260,000", "500,000"],
            ],
            cell_borders=True,
            size=11,
        )
    )
    return builder.render()


def _long_table_document(n_rows: int = 60, n_cols: int = 5) -> bytes:
    builder = DocumentBuilder(
        margins=PageMargins(48, 48, 48, 48), created=_FIXED_CREATED
    )
    flow = builder.flow()
    products = ["Widget", "Gadget", "Sprocket", "Bolt", "Cable"]
    header = ["SKU", "Product", "Category", "Price", "Stock"]
    rows = []
    for row in range(n_rows):
        rows.append(
            [
                "SKU-%03d" % row,
                products[row % len(products)],
                "A" if row % 2 == 0 else "B",
                "%.2f" % (9.99 + row * 1.5),
                str(100 + row),
            ]
        )
    flow.table(
        TableLayout(col_widths=[60, 90, 90, 90, 60], header=header, rows=rows, size=9)
    )
    return builder.render()


def _images_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED)
    flow = builder.flow()
    png = _gradient_png()
    flow.text("Embedded raster images", size=16, color=(0.1, 0.1, 0.1))
    flow.image(png, x=0, y=30, width=128, height=128)
    flow.image(_synthetic_jpeg(), x=150, y=30, width=64, height=64)
    flow.image(png, x=0, y=200, width=128, height=128)
    return builder.render()


_EMBEDDED_TEXT = (
    "Embedded Liberation Sans: the quick brown fox jumps over the lazy dog. "
    "Unicode: caf\u00e9 r\u00e9sum\u00e9 na\u00efve \u2013 en dash "
    "\u2014 em dash well-known hyphenated words 1234567890."
)


def _embedded_text_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED, mode_embed_fonts=True)
    flow = builder.flow()
    flow.text("Embedded fonts (phase 3)", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_EMBEDDED_TEXT, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _unicode_hyphen_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED, mode_embed_fonts=True)
    flow = builder.flow()
    flow.text("Unicode and hyphens", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "W\u00f6rld \u2013 wide en-dash \u2014 em-dash; well-known, "
        "e.g. caf\u00e9 3\u20134 items, 123-456-7890.",
        size=12,
        color=(0.15, 0.15, 0.15),
    )
    return builder.render()


# ---------------------------------------------------------------------------
# Phase 4: PDF/A-4 compliant fixtures (XMP + ICC + OutputIntent, no /Info)
# ---------------------------------------------------------------------------


def _pdfa4_minimal_text_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED, mode_pdfa4=True)
    flow = builder.flow()
    flow.text("PDF/A-4 minimal text document", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_EMBEDDED_TEXT, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _pdfa4_table_simple_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED, mode_pdfa4=True)
    flow = builder.flow()
    flow.text("PDF/A-4 three by three table", size=16, color=(0.1, 0.1, 0.1))
    flow.table(
        TableLayout(
            col_widths=[120, 140, 140],
            header=["Metric", "Q2", "Q3"],
            rows=[
                ["Revenue", "1,240,000", "1,510,000"],
                ["Expenses", "980,000", "1,010,000"],
                ["Profit", "260,000", "500,000"],
            ],
            cell_borders=True,
            size=11,
        )
    )
    return builder.render()


def _pdfa4_figure_image_document() -> bytes:
    builder = DocumentBuilder(created=_FIXED_CREATED, mode_pdfa4=True)
    flow = builder.flow()
    png = _gradient_png()
    flow.text("PDF/A-4 embedded raster images", size=16, color=(0.1, 0.1, 0.1))
    flow.image(png, x=0, y=30, width=128, height=128)
    flow.image(_synthetic_jpeg(), x=150, y=30, width=64, height=64)
    flow.image(png, x=0, y=200, width=128, height=128)
    return builder.render()


# ---------------------------------------------------------------------------
# Phase 5: PDF/UA-2 fixtures (dual mode: PDF/A-4 + PDF/UA-2 together)
# ---------------------------------------------------------------------------

_ALT_GRADIENT = "RGB gradient demonstration image"
_ALT_JPEG = "Synthetic baseline JPEG sample"


def _phase5_minimal_text_document() -> bytes:
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Minimal text document",
    )
    flow = builder.flow()
    flow.text("PDF/UA-2 minimal text document", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_EMBEDDED_TEXT, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _phase5_heading_title_document() -> bytes:
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Quarterly Report - Q3 FY26",
    )
    flow = builder.flow()
    flow.text(_HEADING, size=18, color=(0.15, 0.25, 0.45))
    flow.paragraph(_PARAGRAPH, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _phase5_table_simple_document() -> bytes:
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Three by three table",
    )
    flow = builder.flow()
    flow.text("PDF/UA-2 three by three table", size=16, color=(0.1, 0.1, 0.1))
    flow.table(
        TableLayout(
            col_widths=[120, 140, 140],
            header=["Metric", "Q2", "Q3"],
            rows=[
                ["Revenue", "1,240,000", "1,510,000"],
                ["Expenses", "980,000", "1,010,000"],
                ["Profit", "260,000", "500,000"],
            ],
            cell_borders=True,
            size=11,
        )
    )
    return builder.render()


def _phase5_table_multipage_document() -> bytes:
    builder = DocumentBuilder(
        margins=PageMargins(48, 48, 48, 48),
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Long multipage table",
    )
    flow = builder.flow()
    products = ["Widget", "Gadget", "Sprocket", "Bolt", "Cable"]
    header = ["SKU", "Product", "Category", "Price", "Stock"]
    rows = []
    for row in range(60):
        rows.append(
            [
                "SKU-%03d" % row,
                products[row % len(products)],
                "A" if row % 2 == 0 else "B",
                "%.2f" % (9.99 + row * 1.5),
                str(100 + row),
            ]
        )
    flow.text(
        "Inventory — Q3 2026",
        size=18,
        color=(0.1, 0.1, 0.1),
    )
    flow.text(
        "Warehouse stock levels by product line",
        size=11,
        color=(0.35, 0.35, 0.35),
    )
    flow.table(
        TableLayout(
            col_widths=[80, 120, 100, 120, 79],
            header=header,
            rows=rows,
            size=9,
            text_color=(0.12, 0.14, 0.16),
            row_background=lambda i: (0.97, 0.97, 0.97) if i % 2 else None,
            grid=True,
        )
    )
    return builder.render()


def _phase5_figure_alt_document() -> bytes:
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Figures with alternative text",
    )
    flow = builder.flow()
    png = _gradient_png()
    flow.text("PDF/UA-2 figures with alternative text", size=16, color=(0.1, 0.1, 0.1))
    flow.image(png, x=0, y=30, width=128, height=128, alt=_ALT_GRADIENT)
    flow.image(_synthetic_jpeg(), x=150, y=30, width=64, height=64, alt=_ALT_JPEG)
    flow.image(png, x=0, y=200, width=128, height=128, alt=_ALT_GRADIENT)
    return builder.render()


def _phase5_link_annot_document() -> bytes:
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Links and annotations",
    )
    flow = builder.flow()
    flow.text("PDF/UA-2 links and annotations", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "This paragraph precedes the link and gives the page body content.",
        size=11,
        color=(0.15, 0.15, 0.15),
    )
    flow.link(
        "Visit the pythoncoreengine project page",
        "https://example.com/pythoncoreengine",
        size=11,
        color=(0.05, 0.05, 0.85),
    )
    return builder.render()


# ---------------------------------------------------------------------------
# Phase 7: bookmarks/outlines and AcroForm widgets
# ---------------------------------------------------------------------------

# Long body text that spans at least two pages (the outline fixture needs
# destinations on page 1).
_PHASE7_BODY = (
    "Section body text for the bookmarks fixture. " * 200
    + "A second paragraph to push the flow onto another page. " * 200
)


def _phase7_bookmarks_document() -> bytes:
    """Dual-mode document with a 3-level outline tree and /PageMode.

    Destinations are structure destinations: each item jumps to a ``/Sect``
    element, satisfying the PDF/UA-2 clause 8.8 rule that every in-document
    destination shall be a structure destination.
    """
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Phase 7 bookmarks",
        outlines=True,
        page_mode="UseOutlines",
    )
    flow = builder.flow()
    flow.text("PDF/UA-2 bookmarks", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_PHASE7_BODY, size=11, color=(0.15, 0.15, 0.15))
    report = builder.add_outline("Quarterly Report", page_index=0)
    overview = builder.add_outline("Overview", page_index=0, parent=report)
    builder.add_outline("Highlights", page_index=1, y=600, parent=overview)
    builder.add_outline("Trade Summary", page_index=1, y=300)
    return builder.render()


def _phase7_bookmarks_nocomply_document() -> bytes:
    """Plain PDF 2.0 outline tree with page destinations (no structure)."""
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        outlines=True,
        page_mode="UseOutlines",
    )
    flow = builder.flow()
    flow.text("Plain bookmarks", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_PHASE7_BODY, size=11, color=(0.15, 0.15, 0.15))
    report = builder.add_outline("Quarterly Report", page_index=0)
    overview = builder.add_outline("Overview", page_index=0, parent=report)
    builder.add_outline("Highlights", page_index=1, y=600, parent=overview)
    builder.add_outline("Trade Summary", page_index=1, y=300)
    return builder.render()


def _phase7_form_document() -> bytes:
    """Dual-mode document with one text field and one checkbox.

    The text widget carries a value plus an appearance stream drawn with
    the embedded font; the checkbox provides ``/Yes`` and ``/Off`` state
    appearances.  Both widgets are enclosed by ``/Form`` structure
    elements so PDF/UA-2 clause 8.10.1 stays satisfied.
    """
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Phase 7 interactive form",
        forms=True,
    )
    flow = builder.flow()
    flow.text("PDF/UA-2 interactive form", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "Fill in the fields below and submit. " * 8,
        size=11,
        color=(0.15, 0.15, 0.15),
    )
    builder.add_text_field(
        "ClientName",
        "Jane Doe",
        page_index=0,
        x=100,
        y=150,
        width=200,
        height=18,
        size=10,
    )
    builder.add_checkbox(
        "Consent", page_index=0, x=100, y=180, width=12, height=12, checked=True
    )
    return builder.render()


def _phase7_form_nocomply_document() -> bytes:
    """Plain PDF 2.0 AcroForm: widgets without structure or /Form elements."""
    builder = DocumentBuilder(created=_FIXED_CREATED, forms=True)
    flow = builder.flow()
    flow.text("Plain interactive form", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "Fill in the fields below. " * 8,
        size=11,
        color=(0.15, 0.15, 0.15),
    )
    builder.add_text_field(
        "ClientName",
        "Jane Doe",
        page_index=0,
        x=100,
        y=150,
        width=200,
        height=18,
        size=10,
    )
    builder.add_checkbox(
        "Consent", page_index=0, x=100, y=180, width=12, height=12, checked=False
    )
    return builder.render()


# ---------------------------------------------------------------------------
# Phase 7.4: digitally signed fixtures (seeded RSA key, fixed signing time)
# ---------------------------------------------------------------------------

#: The seed of the deterministic 2048-bit signing key (test/dev only --
#: NOT production security; see engine.crypto).
_SIGNING_KEY_SEED = 7


def _signed_document(*, compliant: bool) -> bytes:
    """A document with a signed signature field, deterministic bytes.

    The seeded key and the fixed signing time make the output
    byte-identical across runs and machines.  The signature dictionary
    carries /Reason, /Location, /ContactInfo and /Name meta entries.
    """
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=compliant,
        mode_pdfua2=compliant,
        title="Phase 7.4 signed document" if compliant else None,
        signing=True,
    )
    flow = builder.flow()
    flow.text("Digitally signed document", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "This document is covered by a byte-range digital signature. "
        "The CMS (PKCS#7) payload was produced entirely in pure Python. " * 12,
        size=11,
        color=(0.15, 0.15, 0.15),
    )
    builder.add_signature_field(
        "Signature1",
        page_index=0,
        x=100,
        y=120,
        width=200,
        height=18,
        reason="Phase 7.4 fixture approval",
        location="pythoncoreengine CI",
        contact_info="qa@example.com",
        signer_name="pythoncoreengine",
    )
    placeholder = builder.render()
    return sign_pdf(
        placeholder,
        generate_rsa_key(2048, seed=_SIGNING_KEY_SEED),
        signing_time=_FIXED_CREATED,
    )


def _phase7_signed_document() -> bytes:
    """Dual-mode (A-4 + UA-2) document with a valid digital signature."""
    return _signed_document(compliant=True)


def _phase7_signed_nocomply_document() -> bytes:
    """Plain PDF 2.0 document with a valid digital signature."""
    return _signed_document(compliant=False)


# ---------------------------------------------------------------------------
# Phase 7.5: encrypted fixtures (deterministic seeds, byte-stable output)
# ---------------------------------------------------------------------------


def _encrypted_document(*, revision: int) -> bytes:
    """A plain-PDF-2.0 document encrypted with the /Standard handler.

    The seeded EncryptSpec makes every salt, the AES-256 file key and
    every per-object IV deterministic, so the emitted bytes -- and hence
    the fixture md5 -- are identical across runs and machines.  Revision
    4 is AES-128 (/AESV2), revision 6 is AES-256 (/AESV3).
    """
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        encrypt=EncryptSpec(
            password="fixture-password",
            revision=revision,
            seed=b"phase7.5-r%d" % revision,
        ),
    )
    flow = builder.flow()
    flow.text(
        "Encrypted document (%s)"
        % ("AES-128" if revision == 4 else "AES-256"),
        size=16,
        color=(0.1, 0.1, 0.1),
    )
    flow.paragraph(_PARAGRAPH, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _encrypted_r4_document() -> bytes:
    """The AES-128 (revision 4) encrypted fixture."""
    return _encrypted_document(revision=4)


def _encrypted_r6_document() -> bytes:
    """The AES-256 (revision 6) encrypted fixture."""
    return _encrypted_document(revision=6)


# ---------------------------------------------------------------------------
# Phase 7.6: SVG path -> Form XObject fixtures
# ---------------------------------------------------------------------------

_SVG_STAR = (
    "M90 5 L116.5 52.3 L169.4 60.9 L133.5 96 L143.2 148.7 "
    "L90 125.5 L36.8 148.7 L46.5 96 L10.6 60.9 L63.5 52.3 Z"
)
_SVG_WAVE = "M10 80 Q 95 10 180 80 T 350 80 T 520 80"


def _phase7_svg_document(*, compliant: bool) -> bytes:
    """A document with SVG paths drawn as Form XObjects.

    Compliant variant is dual-mode (A-4 + UA-2): every placement is a
    ``Figure`` with ``/Alt`` text, so it must pass both veraPDF flavours.
    """
    builder = DocumentBuilder(
        created=_FIXED_CREATED,
        mode_pdfa4=compliant,
        mode_pdfua2=compliant,
        title="SVG paths as Form XObjects",
    )
    flow = builder.flow()
    flow.text("SVG path data drawn from core", size=16, color=(0.1, 0.1, 0.1))
    flow.svg(
        _SVG_STAR,
        x=0,
        y=30,
        width=180,
        height=160,
        alt="A five-pointed star",
        fill="#F1C40F",
        stroke="#154360",
        stroke_width=2,
    )
    flow.svg(
        _SVG_WAVE,
        x=0,
        y=220,
        width=520,
        height=80,
        alt="A smooth quadratic wave",
        fill="none",
        stroke="#21618C",
        stroke_width=3,
        transform="translate(0,0)",
    )
    return builder.render()


def _phase7_svg_document_dual() -> bytes:
    """Dual-mode (A-4 + UA-2) document with SVG figures."""
    return _phase7_svg_document(compliant=True)


def _phase7_svg_document_nocomply() -> bytes:
    """Plain PDF 2.0 document with SVG figures."""
    return _phase7_svg_document(compliant=False)


def generate_fixtures(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Path]:
    """Write all phase-1 to phase-5 fixtures; returns {fixture name: path}."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}

    builders = {
        "phase1_minimal.pdf": generate_minimal_pdf(
            "Hello from pythoncoreengine", created=_FIXED_CREATED
        ),
        "phase2_title_paragraph.pdf": _title_paragraph_document(),
        "phase2_table_3x3.pdf": _table_3x3_document(),
        "phase2_table_long.pdf": _long_table_document(),
        "phase2_images.pdf": _images_document(),
        "phase3_embedded_text.pdf": _embedded_text_document(),
        "phase3_unicode_hyphen.pdf": _unicode_hyphen_document(),
        "phase4_minimal_text.pdf": _pdfa4_minimal_text_document(),
        "phase4_table_simple.pdf": _pdfa4_table_simple_document(),
        "phase4_figure_image.pdf": _pdfa4_figure_image_document(),
        "phase5_minimal_text.pdf": _phase5_minimal_text_document(),
        "phase5_heading_title.pdf": _phase5_heading_title_document(),
        "phase5_table_simple.pdf": _phase5_table_simple_document(),
        "phase5_table_multipage.pdf": _phase5_table_multipage_document(),
        "phase5_figure_alt.pdf": _phase5_figure_alt_document(),
        "phase5_link_annot.pdf": _phase5_link_annot_document(),
        "phase7_bookmarks.pdf": _phase7_bookmarks_document(),
        "phase7_bookmarks_nocomply.pdf": _phase7_bookmarks_nocomply_document(),
        "phase7_form.pdf": _phase7_form_document(),
        "phase7_form_nocomply.pdf": _phase7_form_nocomply_document(),
        "phase7_signed.pdf": _phase7_signed_document(),
        "phase7_signed_nocomply.pdf": _phase7_signed_nocomply_document(),
        "phase7_encrypted.pdf": _encrypted_r4_document(),
        "phase7_encrypted_256.pdf": _encrypted_r6_document(),
        "phase7_svg.pdf": _phase7_svg_document_dual(),
        "phase7_svg_nocomply.pdf": _phase7_svg_document_nocomply(),
    }
    for name, data in builders.items():
        target = output_dir / name
        target.write_bytes(data)
        paths[name] = target

    return paths


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_DIR
    for name, path in generate_fixtures(out_dir).items():
        print(f"wrote {path} ({path.stat().st_size} bytes)")

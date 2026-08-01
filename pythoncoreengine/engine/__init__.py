"""pythoncoreengine -- pure-Python PDF 2.0 writer (stdlib only, no third-party deps).

Phase 1 scope: a minimal untagged single-page PDF 2.0 file with a valid
header, catalog, pages tree, one page, a simple text content stream, a
placeholder standard Type1 font, classic xref and a trailer with ``/ID``.

Phase 2 scope: real drawing -- positioned coloured text, word wrapping,
margins and a content box, fixed-column tables with borders and header
styling, multi-page flow with per-page content streams and resources, and
raster images (JPEG passthrough, decoded PNG re-encoded as FlateDecode).

Phase 3 scope: TrueType font embedding -- a pure-stdlib TTF parser
(:class:`TTFFont`), glyph subsetting (:class:`TTFSubsetter`) with correct
composite handling and sfnt checksums, the per-document
:class:`FontRegistry` with Liberation substitution for the standard
Helvetica/Times/Courier faces, and the Type0/CIDFontType2/FontDescriptor/
FontFile2/ToUnicode object chain with Identity-H hex text in content
streams.

Phase 4 scope: PDF/A-4 (ISO 19005-4) compliance -- an XMP metadata packet
(engine.meta), ICC sRGB + Gray profiles generated in code (engine.color),
the OutputIntent and A-4 rule wiring (engine.pdfa).  ``DocumentBuilder`` /
``build_minimal_document`` gain ``mode_pdfa4=True``: embedded fonts are
forced, the trailer /Info is omitted, and the metadata stream, both ICC
streams and the OutputIntent are reserved up front and referenced from the
catalog and every page's DefaultRGB/DefaultGray resources; image colour
spaces are rewritten to ICCBased arrays.

Phase 5 scope: tagged PDF 2.0 with a PDF/UA-2 (ISO 14289-2:2024) claim
(``mode_pdfua2=True``; A-4 mode implies tagging).  engine.structure owns
the StructureManager -- per-page MCIDs, the ParentTree number tree, the
StructElem objects (Document/H1/P/Table/TR/TH/TD/Figure/Link) and the PDF
2.0 Namespace -- while the catalog gains /Lang, /MarkInfo, /StructTreeRoot
and /ViewerPreferences, pages gain /StructParents (and /Tabs /S with
annotations), content streams gain BDC/EMC marked content with artifact
wrapping for decorative graphics, and the XMP packet carries the pdfuaid
identification plus the pdfaExtension schema registration.

Phase 6 scope: performance & pooling -- bounded name/number/reference
formatting caches (engine.write), preallocated and pooled final-PDF
buffers plus a reused xref offset list (engine.doc), parallel content
stream compression with a bounded worker pool (byte-deterministic vs
serial), the structure hot path (per-cell ``begin_cell``/``begin_mcid``,
fast-shape StructElem serialization), process-level cached ICC profiles
and XMP packet prefix, and the optional bounded font subset cache
(engine.font).  ``python3 -m engine.bench`` records dense-table timing
and peak heap (tracemalloc) into ``baselines/bench_python.txt``.

Phase 7 scope: optional product features, all off by default --
``outlines=True`` adds the ``/Outlines`` bookmark tree with optional
``/PageMode`` (engine.outline), ``forms=True`` adds an ``/AcroForm``
with widget annotations and embedded-font appearance streams
(engine.form), and ``signing=True`` adds a digital-signature field whose
placeholder document is signed in a post-pass by
``engine.signature.sign_pdf`` (byte-range + CMS).  Tagged output keeps
PDF/UA-2 green: outline destinations become structure destinations
(``/Sect`` elements) and every widget is enclosed by a ``/Form``
structure element.

Phase 7.4 scope: pure-stdlib digital signatures.  engine.crypto owns the
crypto primitives -- seeded deterministic RSA key generation, PKCS#1 v1.5
padding, RSA-SHA256 sign/verify, a minimal DER writer and the CMS/PKCS#7
SignedData builder -- and engine.signature owns the PDF integration: the
``/FT /Sig`` field, the signature dictionary with fixed-width
``/ByteRange``/``/Contents`` placeholders, and the length-preserving
byte-range splice.  Signed dual-mode fixtures pass both ``verapdf -f 4``
and ``verapdf -f ua2``.  This signing is NOT production security: keys
are seeded and no certificate chain is emitted (see engine.crypto).

Public API:

- ``generate_minimal_pdf(text="Hello") -> bytes`` -- the phase-1 entry point.
- ``DocumentBuilder`` -- flow-driven multi-page builder: create a
  ``PageFlow`` via ``builder.flow()``, draw with ``flow.paragraph`` /
  ``flow.table`` / ``flow.image`` / ``flow.link``, then ``builder.render()``.
- ``PageFlow`` / ``TableLayout`` / ``PageMargins`` / ``wrap_text`` --
  layout primitives.
- ``parse_jpeg`` / ``decode_png`` / ``parse_image`` -- image decoding.
- ``FontRegistry`` / ``TTFFont`` / ``standard_font_name`` -- font parsing,
  subsetting and the Liberation map (phase 3).
- ``ICCProfile`` / ``build_xmp_packet`` / ``output_intent_dict`` --
  PDF/A-4 object builders (phase 4).
- ``StructureManager`` / ``StructElem`` -- tagged-structure bookkeeping
  (phase 5).
- ``OutlineManager`` / ``OutlineItem`` -- bookmarks/outlines (phase 7.1).
- ``FormManager`` / ``FormField`` -- AcroForm widgets (phase 7.3).
- ``sign_pdf`` / ``SignatureManager`` -- byte-range digital signatures
  (phase 7.4).
- ``generate_rsa_key`` / ``RSAPrivateKey`` / ``build_cms_signed_data`` --
  pure-stdlib RSA + CMS crypto (phase 7.4).
- Mode flags: ``ModePDF20`` (always on), ``ModePDFA4``, ``ModePDFUA2``,
  ``ModeEmbedFonts`` (per-document switches via the builder kwargs).

Explicitly out of scope in this phase: PDF/A-4e/4f, encryption and
certificate-backed signature trust (signatures use seeded, uncertified
keys; no X.509 chain is emitted).
"""

from __future__ import annotations

from typing import Optional, Tuple

from .color import ICCProfile, gray_icc, srgb_icc
from .content import ContentStream
from .crypto import (
    RSAPrivateKey,
    RSAPublicKey,
    build_cms_signed_data,
    generate_rsa_key,
    rsa_sign_pkcs1v15,
    rsa_verify_pkcs1v15,
)
from .doc import (
    PRODUCER,
    Document,
    DocumentBuilder,
    build_minimal_document,
    encode_info_dict,
)
from .font import (
    FontEntry,
    FontRegistry,
    LIBERATION_FONT_DIR,
    LIBERATION_FONT_PATHS,
    STANDARD_TO_LIBERATION,
    TTFFont,
    TTFSubsetter,
    standard_font_name,
)
from .form import FormField, FormManager
from .image import ImageInfo, JPEGImage, PNGImage, decode_png, parse_image, parse_jpeg
from .layout import (
    FlowHost,
    PageFlow,
    PageMargins,
    TableLayout,
    text_width,
    wrap_text,
)
from .meta import XMP_PACKET_ID, build_xmp_dict, build_xmp_packet
from .outline import OutlineItem, OutlineManager
from .page import A4_POINTS, DEFAULT_PAGE_SIZE, Font, Page, PagesTree
from .pdfa import (
    A4_OUTPUT_CONDITION_IDENTIFIER,
    A4_REGISTRY_NAME,
    OutputIntent,
    default_colorspaces,
    icc_based_colorspace,
    metadata_stream_dict,
    output_intent_dict,
    rewrite_image_colorspace,
)
from .signature import SignatureManager, sign_pdf
from .structure import PDF2_SSN_NAMESPACE, StructElem, StructureManager
from .write import B, ByteWriter, N, ObjectId, PdfBool, PdfHexString, PdfName

__version__ = "0.7.4"

# Document mode flags (module-level capability markers).  ModePDF20 is
# always on; ModePDFA4 / ModeEmbedFonts activate per-document through the
# Document/DocumentBuilder constructor kwargs.
ModePDF20: bool = True
ModePDFA4: bool = False
ModePDFUA2: bool = False
ModeEmbedFonts: bool = False

__all__ = [
    "__version__",
    "A4_OUTPUT_CONDITION_IDENTIFIER",
    "A4_POINTS",
    "A4_REGISTRY_NAME",
    "B",
    "ByteWriter",
    "ContentStream",
    "DEFAULT_PAGE_SIZE",
    "Document",
    "DocumentBuilder",
    "FlowHost",
    "Font",
    "FontEntry",
    "FontRegistry",
    "FormField",
    "FormManager",
    "ICCProfile",
    "ImageInfo",
    "JPEGImage",
    "LIBERATION_FONT_DIR",
    "LIBERATION_FONT_PATHS",
    "ModeEmbedFonts",
    "ModePDF20",
    "ModePDFA4",
    "ModePDFUA2",
    "N",
    "ObjectId",
    "OutputIntent",
    "OutlineItem",
    "OutlineManager",
    "PDF2_SSN_NAMESPACE",
    "PRODUCER",
    "PNGImage",
    "Page",
    "PageFlow",
    "PageMargins",
    "PagesTree",
    "PdfBool",
    "PdfHexString",
    "PdfName",
    "RSAPrivateKey",
    "RSAPublicKey",
    "STANDARD_TO_LIBERATION",
    "SignatureManager",
    "StructElem",
    "StructureManager",
    "TTFFont",
    "TTFSubsetter",
    "TableLayout",
    "XMP_PACKET_ID",
    "build_cms_signed_data",
    "build_minimal_document",
    "build_xmp_dict",
    "build_xmp_packet",
    "decode_png",
    "default_colorspaces",
    "encode_info_dict",
    "generate_minimal_pdf",
    "generate_rsa_key",
    "gray_icc",
    "icc_based_colorspace",
    "metadata_stream_dict",
    "output_intent_dict",
    "parse_image",
    "parse_jpeg",
    "rewrite_image_colorspace",
    "rsa_sign_pkcs1v15",
    "rsa_verify_pkcs1v15",
    "sign_pdf",
    "srgb_icc",
    "standard_font_name",
    "text_width",
    "wrap_text",
]


def generate_minimal_pdf(
    text: str = "Hello",
    *,
    page_size: Optional[Tuple[float, float]] = None,
    created: Optional[object] = None,
) -> bytes:
    """Generate a single-page minimal PDF 2.0 byte stream with valid classic xref.

    Args:
        text: The line of text to draw on the page (ASCII recommended).
        page_size: ``(width, height)`` in points; defaults to A4 portrait.
        created: Fixed ``datetime.datetime`` for /Info dates (deterministic
            output when provided); defaults to the current time.

    Returns:
        Complete PDF bytes: header, six objects, xref, trailer with ``/ID``,
        ``startxref`` and ``%%EOF``.
    """
    size = page_size if page_size is not None else DEFAULT_PAGE_SIZE
    return build_minimal_document(text=text, page_size=size, created=created)


# Phase 7.5: PDF encryption (pure-Python AES/RC4 + /Standard handler).
# The engine-level exports; the underlying primitives live in
# engine.cipher (AES-128/256, CBC, PKCS#7, RC4).
from .cipher import (
    aes_block_decrypt,
    aes_block_encrypt,
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    pkcs7_pad,
    pkcs7_unpad,
    rc4,
)
from .encrypt import (
    ALL_PERMISSIONS,
    EncryptSpec,
    StandardSecurityHandler,
    WrongPasswordError,
    decrypt_pdf,
    encrypt_pdf,
    permission_flags,
)

__all__ += [
    "ALL_PERMISSIONS",
    "EncryptSpec",
    "StandardSecurityHandler",
    "WrongPasswordError",
    "aes_block_decrypt",
    "aes_block_encrypt",
    "aes_cbc_decrypt",
    "aes_cbc_encrypt",
    "decrypt_pdf",
    "encrypt_pdf",
    "permission_flags",
    "pkcs7_pad",
    "pkcs7_unpad",
    "rc4",
]

from .svg import (  # noqa: E402
    SVGPathError,
    SVGShape,
    flatten_path,
    parse_path,
    parse_transform,
    path_ops,
    svg_form_xobject,
)

__all__ += [
    "SVGPathError",
    "SVGShape",
    "flatten_path",
    "parse_path",
    "parse_transform",
    "path_ops",
    "svg_form_xobject",
]

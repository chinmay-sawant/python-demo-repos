"""Document builder: object ID allocation, the object store, final assembly.

:class:`Document` owns object IDs, write order and the emit path
(header -> objects in ID order -> classic xref -> trailer -> startxref).

Phase 2 adds :class:`DocumentBuilder`, a flow-driven multi-page builder:
layout runs against a :class:`PageFlow` (engine.layout) which calls back for
new pages, fonts and images.  All object IDs are reserved during layout in
emission order and bodies are attached at :meth:`DocumentBuilder.render`
time, which keeps the reserve-then-attach invariant without freezing page
contents before the flow finishes.  Later phases (fonts, compliance and
tagging objects) slot into the same reserve-then-attach sequence.

Phase 5 wires the tagged path: ``mode_pdfua2`` (and, since A-4 implies
tagged, ``mode_pdfa4``) attaches a :class:`~engine.structure.StructureManager`
whose Namespace/ParentTree/StructTreeRoot IDs are reserved before the
catalog; the flow then emits BDC/EMC marked content and structure elements,
pages get ``/StructParents``, and the catalog carries ``/Lang``,
``/MarkInfo``, ``/StructTreeRoot`` and ``/ViewerPreferences`` for the UA-2
claim.

Phase 6 adds the performance & pooling layer: ``Document.render`` computes
a byte-size estimate from the already-attached bodies and preallocates the
final buffer, reuses its xref-offset list and digests the buffer in place;
``DocumentBuilder`` gains ``parallel_compress`` (page content streams are
compressed concurrently with a bounded worker pool, emitted in page order
so the bytes stay deterministic).

Phase 7 adds the optional product features, all off by default:
``outlines=True`` wires an ``/Outlines`` tree (engine.outline) plus
optional ``/PageMode``, ``forms=True`` wires an ``/AcroForm`` with widget
annotations and appearance streams (engine.form), and ``signing=True``
adds a signature field whose placeholder document is signed in a post-pass
by :func:`engine.signature.sign_pdf` (byte-range + CMS, engine.crypto).
In tagged output the outline destinations become structure destinations
(``/Sect`` elements) and widgets get ``/Form`` structure elements, keeping
PDF/UA-2 green; plain PDF 2.0 output uses page destinations and no
structure.
"""

from __future__ import annotations

import datetime
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .color import ICCProfile
from .content import ContentStream
from .encrypt import EncryptSpec, encrypt_pdf
from .font import FontChain, FontRegistry, standard_font_name
from .form import FormField, FormManager
from .image import ImageInfo, parse_image
from .layout import PageFlow, PageMargins
from .meta import build_xmp_packet
from .outline import OutlineItem, OutlineManager
from .page import DEFAULT_PAGE_SIZE, Font, Page, PagesTree
from .pdfa import (
    default_colorspaces,
    metadata_stream_dict,
    output_intent_dict,
    rewrite_image_colorspace,
)
from .signature import SignatureManager
from .structure import StructureManager, link_annotation_dict
from .write import (
    B,
    ByteWriter,
    N,
    ObjectId,
    PdfHexString,
    PdfName,
    compressed_stream,
    encode_dict,
    encode_object,
    encode_stream_object,
    encode_xref_section,
    format_date,
)

__all__ = [
    "Document",
    "DocumentBuilder",
    "PRODUCER",
    "build_minimal_document",
    "encode_info_dict",
]

PRODUCER = "pythoncoreengine 0.1.0"

# Default text origin on the page (points from the lower-left corner).
_TEXT_ORIGIN: Tuple[float, float] = (72.0, 760.0)
_FONT_SIZE = 12.0

# Default dc:title for the XMP packet when the caller gives no title.
_DEFAULT_TITLE = "pythoncoreengine document"

# Standard Type1 font names available per page resource in this phase.
# Under embed mode these names map through the registry to Liberation faces.
_FONT_TABLE = {"F1": "Helvetica", "F2": "Helvetica-Bold"}


def encode_info_dict(created: Optional[datetime.datetime] = None) -> Dict[Any, Any]:
    """Build the non-A ``/Info`` dict with producer and PDF dates.

    ``/CreationDate`` and ``/ModDate`` use the ``D:YYYYMMDDHHmmSS`` form;
    under PDF/A-4 mode (phase 4) the Info dict must be omitted entirely.
    """
    when = created if created is not None else datetime.datetime.now()
    return {
        N("Producer"): PRODUCER,
        N("CreationDate"): format_date(when),
        N("ModDate"): format_date(when),
    }


class Document:
    """Collects indirect objects and assembles a complete PDF 2.0 byte stream.

    Mode flags (phase 1: stubs).  ``mode_pdf20`` is always on; the compliance
    and font-embedding modes activate in later phases and currently change
    nothing about the emitted bytes.

    Phase 6: :meth:`render` is allocation-conscious.  It estimates the final
    size from the attached bodies (bodies are already encoded ``bytes``, so
    the estimate is nearly exact), reuses its own byte buffer and xref
    offset list across renders (document-level pooling, no global state)
    and digests the buffer in place instead of copying it twice.
    """

    mode_pdf20: bool = True
    mode_pdfa4: bool = False
    mode_pdfua2: bool = False
    mode_embed_fonts: bool = False

    def __init__(
        self,
        *,
        mode_pdfa4: bool = False,
        mode_pdfua2: bool = False,
        mode_embed_fonts: bool = False,
    ) -> None:
        self.mode_pdfa4 = mode_pdfa4
        self.mode_pdfua2 = mode_pdfua2
        self.mode_embed_fonts = mode_embed_fonts
        self._next_number = 1
        self._objects: List[Tuple[int, bytes]] = []
        self._root: Optional[ObjectId] = None
        self._info: Optional[ObjectId] = None
        self._pooled_buffer: Optional[bytearray] = None
        self._pooled_offsets: List[int] = []

    # ------------------------------------------------------------------
    # Object allocation and storage
    # ------------------------------------------------------------------

    def reserve(self) -> ObjectId:
        """Allocate the next object ID; attach its body later via set_value/set_stream.

        Reserving ahead of encoding lets assembly reference objects (e.g. the
        catalog pointing at the pages tree) before their bodies are built,
        while keeping the planned emission order.
        """
        obj_id = ObjectId(self._next_number)
        self._next_number += 1
        return obj_id

    def set_value(self, obj_id: ObjectId, value: Any) -> None:
        """Encode ``value`` as the body of the reserved object ``obj_id``.

        Bodies must be attached in ascending ID order (matching the reserve
        order), which keeps the emit path trivially ordered.
        """
        self._attach(obj_id, encode_object(obj_id.number, value))

    def set_stream(
        self,
        obj_id: ObjectId,
        data: bytes,
        stream_dict: Optional[Dict[Any, Any]] = None,
    ) -> None:
        """Encode ``data`` as the body of a stream object ``obj_id``."""
        self._attach(obj_id, encode_stream_object(obj_id.number, data, stream_dict))

    def add_value(self, value: Any) -> ObjectId:
        """Reserve and immediately attach an object; returns its reference."""
        obj_id = self.reserve()
        self.set_value(obj_id, value)
        return obj_id

    def add_stream(
        self,
        data: bytes,
        stream_dict: Optional[Dict[Any, Any]] = None,
    ) -> ObjectId:
        """Reserve and immediately attach a stream object; returns its reference."""
        obj_id = self.reserve()
        self.set_stream(obj_id, data, stream_dict)
        return obj_id

    def _attach(self, obj_id: ObjectId, body: bytes) -> None:
        expected = len(self._objects) + 1
        if obj_id.number != expected:
            raise ValueError(
                f"objects must be attached in ascending ID order; "
                f"expected {expected}, got {obj_id.number}"
            )
        self._objects.append((obj_id.number, body))

    def set_root(self, root_ref: ObjectId) -> None:
        """Declare the catalog reference that the trailer ``/Root`` will point at."""
        self._root = root_ref

    def set_info(self, info_ref: ObjectId) -> None:
        """Declare the ``/Info`` dictionary reference (omitted under PDF/A-4 later)."""
        self._info = info_ref

    # ------------------------------------------------------------------
    # Final assembly
    # ------------------------------------------------------------------

    def render(self) -> bytes:
        """Assemble the final PDF bytes: header, objects, xref, trailer, startxref.

        The two ``/ID`` entries are derived deterministically: md5 (16 bytes,
        rendered as 32 hex chars) over every byte emitted before the trailer.

        Phase 6: the final buffer is sized from a near-exact estimate (the
        attached bodies are already encoded) and the pooled bytearray from a
        previous render is reused when it is big enough; the xref offsets go
        into a reused list instead of a fresh dict.  ``ENGINE_DEBUG_BUFFERS=1``
        logs the buffer length/capacity high-water mark per render.
        """
        if self._root is None:
            raise ValueError("document root (catalog) must be set before render()")
        size = len(self._objects) + 1
        if len(self._pooled_offsets) < size:
            self._pooled_offsets = [0] * size
        offsets = self._pooled_offsets
        offsets[0] = 0

        estimate = sum(len(body) for _number, body in self._objects)
        estimate += size * 40 + 256  # obj headers/footers + xref entries + trailer
        buffer = self._pooled_buffer
        if buffer is None or len(buffer) < estimate:
            buffer = bytearray(estimate)
        writer = ByteWriter(buffer)
        writer.write(b"%PDF-2.0\n")
        writer.write(b"%\xe2\xe3\xcf\xd3\n")  # binary comment line (bytes >= 128)

        for number, body in self._objects:
            offsets[number] = writer.tell()
            writer.write(body)

        xref_section = encode_xref_section(offsets, size)
        xref_offset = writer.tell()

        digest = hashlib.md5()
        writer.feed_digest(digest)
        digest.update(xref_section)
        trailer = self._trailer_dict(size, digest.digest())
        writer.write(xref_section)
        writer.write(b"trailer\n" + encode_dict(trailer) + b"\n")
        writer.write(
            b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
        )
        output = writer.getvalue()
        if os.environ.get("ENGINE_DEBUG_BUFFERS") == "1":
            stats = writer.buffer_stats()
            print(
                "render: %d objects, %d bytes written, high-water %d, "
                "buffer capacity %d"
                % (
                    len(self._objects),
                    stats["length"],
                    stats["high_water"],
                    len(buffer),
                )
            )
        self._pooled_buffer = writer.take_buffer()
        return output

    def _trailer_dict(self, size: int, digest: bytes) -> Dict[PdfName, Any]:
        trailer: Dict[PdfName, Any] = {
            N("Size"): size,
            N("Root"): self._root,
        }
        if self._info is not None:
            trailer[N("Info")] = self._info
        trailer[N("ID")] = [PdfHexString(digest), PdfHexString(digest)]
        return trailer


def build_minimal_document(
    text: str = "Hello",
    *,
    page_size: Tuple[float, float] = DEFAULT_PAGE_SIZE,
    created: Optional[datetime.datetime] = None,
    mode_pdfa4: bool = False,
    mode_pdfua2: bool = False,
    mode_embed_fonts: bool = False,
    title: Optional[str] = None,
) -> bytes:
    """Assemble a single-page minimal PDF 2.0 document.

    Object IDs are reserved in emission order: catalog, pages tree, page,
    content stream, font, info.  Under embed mode the font object is the
    Type0/CIDFontType2 chain (five objects) for a subset of Liberation Sans
    covering ``text``; otherwise it is the phase-1 Type1 placeholder.  Under
    PDF/A-4 mode (``mode_pdfa4``) the metadata, sRGB and gray ICC streams
    and the OutputIntent are reserved up front (before the catalog), fonts
    are forced to the embedded chain and the Info dictionary is omitted.
    Under PDF/UA-2 (``mode_pdfua2``), and whenever A-4 implies tagging, the
    structure tree objects are reserved before the catalog, the page content
    is wrapped in tagged marked content and the catalog carries the UA-2
    entries; ``title`` feeds the XMP ``dc:title`` (a default is used when
    omitted).
    """
    if mode_pdfa4:
        mode_embed_fonts = True
    doc = Document(
        mode_pdfa4=mode_pdfa4,
        mode_pdfua2=mode_pdfua2,
        mode_embed_fonts=mode_embed_fonts,
    )
    if mode_pdfa4 or mode_pdfua2:
        return _build_compliant_minimal_document(
            doc,
            text=text,
            page_size=page_size,
            created=created,
            mode_pdfa4=mode_pdfa4,
            mode_pdfua2=mode_pdfua2,
            title=title,
        )

    if mode_embed_fonts:
        registry = FontRegistry(embed=True)
        entry = registry.entry("F1", "Helvetica")
        entry.add_chars(text)
        registry.generate_subsets()
        chain = FontChain(entry)

    catalog_ref = doc.reserve()
    pages_ref = doc.reserve()
    page_ref = doc.reserve()
    content_ref = doc.reserve()
    font_ref = chain.reserve_ids(doc) if mode_embed_fonts else doc.reserve()
    info_ref = doc.reserve()

    pages = PagesTree([page_ref])
    page = Page(
        parent=pages_ref,
        contents=content_ref,
        width=page_size[0],
        height=page_size[1],
        fonts={Font().resource_name: font_ref},
    )

    stream = ContentStream()
    stream.text_line(
        text,
        x=_TEXT_ORIGIN[0],
        y=_TEXT_ORIGIN[1],
        size=_FONT_SIZE,
        cids=mode_embed_fonts,
    )

    catalog: Dict[Any, Any] = {N("Type"): N("Catalog"), N("Pages"): pages_ref}
    doc.set_value(catalog_ref, catalog)
    doc.set_value(pages_ref, pages.to_dict())
    doc.set_value(page_ref, page.to_dict())
    content_mapper = registry.cid_for if mode_embed_fonts else None
    doc.set_stream(content_ref, stream.render(content_mapper))
    if mode_embed_fonts:
        chain.attach(doc)
    else:
        doc.set_value(font_ref, Font().to_dict())
    doc.set_value(info_ref, encode_info_dict(created))
    doc.set_root(catalog_ref)
    doc.set_info(info_ref)
    return doc.render()


def _build_compliant_minimal_document(
    doc: Document,
    *,
    text: str,
    page_size: Tuple[float, float],
    created: Optional[datetime.datetime],
    mode_pdfa4: bool,
    mode_pdfua2: bool,
    title: Optional[str],
) -> bytes:
    """The minimal single-page build for A-4/UA-2 modes (reserve-queue path).

    Mirrors the phase-1 reserve order (catalog, pages, page, content, font,
    info) with the compliance objects -- and, under tagging, the structure
    objects -- reserved up front, and attaches every body at the end through
    one queue so object IDs stay strictly ascending.
    """
    reserved: List[Tuple[ObjectId, str, Callable[[], Any]]] = []

    def reserve_value(thunk: Callable[[], Any]) -> ObjectId:
        ref = doc.reserve()
        reserved.append((ref, "value", thunk))
        return ref

    def reserve_stream(
        thunk: Callable[[], Tuple[bytes, Optional[Dict[Any, Any]]]]
    ) -> ObjectId:
        ref = doc.reserve()
        reserved.append((ref, "stream", thunk))
        return ref

    registry = FontRegistry(embed=True)
    entry = registry.entry("F1", "Helvetica")
    entry.add_chars(text)
    registry.generate_subsets()
    chain = FontChain(entry)
    chain_host = SimpleNamespace(
        _reserve_value=reserve_value, _reserve_stream=reserve_stream
    )

    page_refs: List[ObjectId] = []
    structure: Optional[StructureManager] = None
    if mode_pdfa4 or mode_pdfua2:
        structure = StructureManager(
            reserve_value=reserve_value, page_ref=lambda index: page_refs[index]
        )

    when = created if created is not None else datetime.datetime.now()
    xmp_ref: Optional[ObjectId] = None
    if mode_pdfa4 or mode_pdfua2:
        xmp_ref = reserve_stream(
            lambda: (
                build_xmp_packet(
                    created=when,
                    producer=PRODUCER,
                    title=title if title is not None else _DEFAULT_TITLE,
                    pdfaid_part=4 if mode_pdfa4 else None,
                    pdfaid_rev=2020 if mode_pdfa4 else None,
                    pdfuaid_part=2 if mode_pdfua2 else None,
                    pdfuaid_rev=2024 if mode_pdfua2 else None,
                ),
                metadata_stream_dict(),
            )
        )
    srgb_ref: Optional[ObjectId] = None
    gray_ref: Optional[ObjectId] = None
    output_intent_ref: Optional[ObjectId] = None
    if mode_pdfa4:
        srgb = ICCProfile.srgb()
        gray = ICCProfile.gray()
        srgb_ref = reserve_stream(
            lambda: (
                compressed_stream(srgb.data),
                {
                    N("N"): srgb.components,
                    N("Alternate"): N(srgb.alternate),
                    N("Filter"): N("FlateDecode"),
                },
            )
        )
        output_intent_ref = reserve_value(lambda: output_intent_dict(srgb_ref))
        gray_ref = reserve_stream(
            lambda: (
                compressed_stream(gray.data),
                {
                    N("N"): gray.components,
                    N("Alternate"): N(gray.alternate),
                    N("Filter"): N("FlateDecode"),
                },
            )
        )

    pages_ref = reserve_value(lambda: PagesTree([page_refs[0]]).to_dict())
    page_holder: List[Optional[Page]] = [None]
    page_ref = reserve_value(lambda: page_holder[0].to_dict())
    content_ref = reserve_stream(
        lambda: (content_raw, {N("Filter"): N("FlateDecode")})
    )
    chain.reserve_thunks(chain_host)

    stream = ContentStream()
    if structure is not None:
        root = structure.document_element()
        elem = structure.create_element("H1", parent=root)
        mcid = structure.begin_content(elem, 0)
        stream.begin_marked_content("H1", {N("MCID"): mcid})
        stream.text_line(
            text,
            x=_TEXT_ORIGIN[0],
            y=_TEXT_ORIGIN[1],
            size=_FONT_SIZE,
            cids=True,
        )
        stream.end_marked_content()
    content_raw = stream.render(registry.cid_for)

    page_holder[0] = Page(
        parent=pages_ref,
        contents=content_ref,
        width=page_size[0],
        height=page_size[1],
        color_spaces=(
            default_colorspaces(srgb_ref, gray_ref) if mode_pdfa4 else None
        ),
        # /StructParents is the page's key (0) in the ParentTree; the page
        # carries MCIDs, so the single page always has one.
        struct_parents=0 if structure is not None else None,
    )
    page_refs.append(page_ref)

    info_ref: Optional[ObjectId] = None
    if not mode_pdfa4:
        info_ref = reserve_value(lambda: encode_info_dict(created))

    catalog: Dict[Any, Any] = {N("Type"): N("Catalog"), N("Pages"): pages_ref}
    if xmp_ref is not None:
        catalog[N("Metadata")] = xmp_ref
    if mode_pdfa4:
        catalog[N("OutputIntents")] = [output_intent_ref]
    if mode_pdfua2:
        catalog[N("Lang")] = "en-US"
        catalog[N("MarkInfo")] = {N("Marked"): B(True)}
        catalog[N("StructTreeRoot")] = structure.tree_root_ref
        catalog[N("ViewerPreferences")] = {N("DisplayDocTitle"): B(True)}
    catalog_ref = reserve_value(lambda: catalog)

    for ref, kind, thunk in reserved:
        if kind == "stream":
            data, extra = thunk()
            doc.set_stream(ref, data, extra)
        else:
            doc.set_value(ref, thunk())
    doc.set_root(catalog_ref)
    if info_ref is not None:
        doc.set_info(info_ref)
    return doc.render()


class DocumentBuilder:
    """Flow-driven multi-page document builder (untagged, phase 2).

    Layout runs through a :class:`PageFlow` obtained from :meth:`flow`;
    the flow calls back into this builder (the :class:`FlowHost`) to create
    pages, register fonts and register/deduplicate image XObjects.

    Object IDs are reserved in emission order while the flow runs; bodies
    are attached at :meth:`render` time in that same order, so the phase-1
    reserve-then-attach invariant holds even though page contents are not
    final until the flow completes.

    Attributes:
        compress: when True (default) content streams are stored with
            ``/Filter /FlateDecode`` via :func:`compressed_stream`.
        parallel_compress: when True (default) and the document has more
            than one page, page content streams are compressed on a bounded
            worker pool (phase 6); output is byte-identical to serial.
    """

    def __init__(
        self,
        *,
        page_size: Tuple[float, float] = DEFAULT_PAGE_SIZE,
        margins: Optional[PageMargins] = None,
        created: Optional[datetime.datetime] = None,
        compress: bool = True,
        parallel_compress: bool = True,
        mode_pdfa4: bool = False,
        mode_pdfua2: bool = False,
        mode_embed_fonts: bool = False,
        title: Optional[str] = None,
        outlines: bool = False,
        page_mode: Optional[str] = None,
        forms: bool = False,
        signing: bool = False,
        encrypt: Optional[EncryptSpec] = None,
    ) -> None:
        # PDF/A-4 requires fully embedded fonts, so the compliant mode
        # forces the phase-3 CID chain on.
        if mode_pdfa4:
            mode_embed_fonts = True
        # A signature field is a form field, so signing implies the
        # AcroForm machinery (widget, /Fields, appearance, structure).
        if signing:
            forms = True
        # Encryption is a plain-PDF-2.0 feature: the A-4 profile forbids
        # encrypted documents (ISO 19005-4), so refuse the combination.
        if mode_pdfa4 and encrypt is not None:
            raise ValueError("encryption is not supported under PDF/A-4")
        self._doc = Document(
            mode_pdfa4=mode_pdfa4,
            mode_pdfua2=mode_pdfua2,
            mode_embed_fonts=mode_embed_fonts,
        )
        self._pdfa = mode_pdfa4
        self._ua2 = mode_pdfua2
        self._page_size = page_size
        self._margins = margins if margins is not None else PageMargins()
        self._created = created
        self._title = title
        self.compress = compress
        self.parallel_compress = parallel_compress
        self._page_mode = page_mode if outlines else None

        self._pages_tree = PagesTree()
        self._page_records: List[Page] = []
        self._page_refs: List[ObjectId] = []
        self._font_refs: Dict[str, ObjectId] = {}
        self._xobject_refs: Dict[str, ObjectId] = {}
        self._image_cache: Dict[bytes, ImageInfo] = {}
        self._image_keys: Dict[Tuple[Any, ...], str] = {}
        self._flow: Optional[PageFlow] = None

        self._font_registry = FontRegistry(embed=self._doc.mode_embed_fonts)
        self._resource_fonts: Dict[str, str] = dict(_FONT_TABLE)
        self._font_names: Dict[str, str] = {
            "Helvetica": "F1",
            "Helvetica-Bold": "F2",
        }

        self._reserved: List[Tuple[ObjectId, str, Callable[[], Any]]] = []
        self._cid_mapper: Optional[Callable[[str, str], int]] = None
        self._structure: Optional[StructureManager] = None
        self._compressed_pages: Optional[List[bytes]] = None
        self._xmp_ref: Optional[ObjectId] = None
        self._icc_srgb_ref: Optional[ObjectId] = None
        self._icc_gray_ref: Optional[ObjectId] = None
        self._output_intent_ref: Optional[ObjectId] = None
        self._outline_manager: Optional[OutlineManager] = None
        self._form_manager: Optional[FormManager] = None
        self._signature_manager: Optional[SignatureManager] = None
        self._encrypt: Optional[EncryptSpec] = encrypt
        if mode_pdfua2 or mode_pdfa4:
            # Tagged output: the structure roots are reserved before the
            # catalog so the catalog can reference the StructTreeRoot.
            self._structure = StructureManager(
                reserve_value=self._reserve_value,
                page_ref=lambda index: self._page_refs[index],
                page_count=lambda: len(self._page_refs),
            )
        if mode_pdfa4 or mode_pdfua2:
            self._reserve_compliance_objects()
        if outlines:
            self._outline_manager = OutlineManager(reserve_value=self._reserve_value)
        if forms:
            self._form_manager = FormManager(
                reserve_value=self._reserve_value,
                reserve_stream=self._reserve_stream,
                page_ref=lambda index: self._page_refs[index],
                annotate=self._add_widget_annotation,
                structure=self._structure,
                page_count=lambda: len(self._page_refs),
                font_ref=self._form_font_ref,
                font_is_cid=lambda name: self._font_registry.font_is_cid(name),
                record_chars=lambda name, text: self._font_registry.record_chars(
                    name, text
                ),
                cid_mapper=lambda: self._cid_mapper,
                compress=lambda: self.compress,
            )
        if signing:
            self._signature_manager = SignatureManager(
                reserve_value=self._reserve_value,
                form_manager=self._form_manager,
            )
        self._catalog_ref = self._reserve_value(lambda: self._catalog_dict())
        self._pages_ref = self._reserve_value(lambda: self._pages_tree.to_dict())
        if not mode_pdfa4:
            self._info_ref = self._reserve_value(lambda: encode_info_dict(self._created))

    # ------------------------------------------------------------------
    # Compliance object set (XMP metadata; ICC profiles + intent under A-4)
    # ------------------------------------------------------------------

    def _reserve_compliance_objects(self) -> None:
        """Reserve the XMP metadata stream (plus A-4 ICC/OutputIntent objects).

        The objects are reserved up front -- before the catalog and any
        page -- so the catalog dictionary and every page resource can
        reference them by reserved ID.  Bodies attach at render time in the
        same order.  The XMP packet carries the pdfaid identification under
        A-4 and the pdfuaid identification plus pdfaExtension registration
        under UA-2; its dates come from ``self._created`` (or the current
        time, matching the non-compliant Info behaviour).
        """
        self._xmp_ref = self._reserve_stream(
            lambda: (
                build_xmp_packet(
                    created=self._created or datetime.datetime.now(),
                    producer=PRODUCER,
                    title=self._title if self._title is not None else _DEFAULT_TITLE,
                    pdfaid_part=4 if self._pdfa else None,
                    pdfaid_rev=2020 if self._pdfa else None,
                    pdfuaid_part=2 if self._ua2 else None,
                    pdfuaid_rev=2024 if self._ua2 else None,
                ),
                metadata_stream_dict(),
            )
        )
        if not self._pdfa:
            return
        self._icc_srgb = ICCProfile.srgb()
        self._icc_gray = ICCProfile.gray()
        self._icc_srgb_ref = self._reserve_stream(
            lambda: (
                compressed_stream(self._icc_srgb.data),
                {
                    N("N"): self._icc_srgb.components,
                    N("Alternate"): N(self._icc_srgb.alternate),
                    N("Filter"): N("FlateDecode"),
                },
            )
        )
        self._output_intent_ref = self._reserve_value(
            lambda: output_intent_dict(self._icc_srgb_ref)
        )
        self._icc_gray_ref = self._reserve_stream(
            lambda: (
                compressed_stream(self._icc_gray.data),
                {
                    N("N"): self._icc_gray.components,
                    N("Alternate"): N(self._icc_gray.alternate),
                    N("Filter"): N("FlateDecode"),
                },
            )
        )

    def _catalog_dict(self) -> Dict[PdfName, Any]:
        """The catalog dictionary plus compliance entries.

        Under A-4: ``/Metadata`` and ``/OutputIntents``.  Under UA-2:
        ``/Lang``, ``/MarkInfo << /Marked true >>``, ``/StructTreeRoot`` and
        ``/ViewerPreferences << /DisplayDocTitle true >>``.  Phase 7: with
        ``outlines=True`` the catalog gains ``/Outlines`` (plus ``/PageMode``
        when ``page_mode`` was given) and with ``forms=True`` ``/AcroForm``.
        """
        catalog: Dict[PdfName, Any] = {
            N("Type"): N("Catalog"),
            N("Pages"): self._pages_ref,
        }
        if self._xmp_ref is not None:
            catalog[N("Metadata")] = self._xmp_ref
        if self._pdfa:
            catalog[N("OutputIntents")] = [self._output_intent_ref]
        if self._ua2:
            catalog[N("Lang")] = "en-US"
            catalog[N("MarkInfo")] = {N("Marked"): B(True)}
            catalog[N("StructTreeRoot")] = self._structure.tree_root_ref
            catalog[N("ViewerPreferences")] = {N("DisplayDocTitle"): B(True)}
        if self._outline_manager is not None:
            catalog[N("Outlines")] = self._outline_manager.outlines_ref
            if self._page_mode is not None:
                catalog[N("PageMode")] = N(self._page_mode)
        if self._form_manager is not None:
            catalog[N("AcroForm")] = self._form_manager.acroform_ref
        return catalog

    def _pdfa_color_spaces(self) -> Optional[Dict[PdfName, Any]]:
        """The per-page DefaultRGB/DefaultGray ICCBased resources (A-4 only)."""
        if not self._pdfa:
            return None
        return default_colorspaces(self._icc_srgb_ref, self._icc_gray_ref)

    # ------------------------------------------------------------------
    # Object reservation (reserve now, attach at render, in reserve order)
    # ------------------------------------------------------------------

    def _reserve_value(self, thunk: Callable[[], Any]) -> ObjectId:
        ref = self._doc.reserve()
        self._reserved.append((ref, "value", thunk))
        return ref

    def _reserve_stream(
        self, thunk: Callable[[], Tuple[bytes, Optional[Dict[Any, Any]]]]
    ) -> ObjectId:
        ref = self._doc.reserve()
        self._reserved.append((ref, "stream", thunk))
        return ref

    # ------------------------------------------------------------------
    # Flow host implementation
    # ------------------------------------------------------------------

    def flow(self) -> PageFlow:
        """Return the (single, lazily created) page flow for this document."""
        if self._flow is None:
            self._flow = PageFlow(
                page_size=self._page_size, margins=self._margins, host=self
            )
        return self._flow

    def new_page(self) -> ContentStream:
        """Reserve a page object and its content stream; return the fresh stream.

        The page dictionary is finalised at render time so that font and
        image resources added later in the flow still land on this page.
        """
        index = len(self._page_records)
        stream_ref = self._reserve_stream(lambda: self._page_stream(index))
        page_ref = self._reserve_value(lambda: self._page_records[index].to_dict())
        page = Page(
            parent=self._pages_ref,
            contents=stream_ref,
            width=self._page_size[0],
            height=self._page_size[1],
            color_spaces=self._pdfa_color_spaces(),
        )
        self._page_records.append(page)
        self._page_refs.append(page_ref)
        self._pages_tree.add_page(page_ref)
        return ContentStream()

    def _page_stream(self, index: int) -> Tuple[bytes, Optional[Dict[Any, Any]]]:
        if self._compressed_pages is not None:
            return self._compressed_pages[index], {N("Filter"): N("FlateDecode")}
        flow = self._flow
        if flow is None:
            raise ValueError("page stream requested before flow() was created")
        raw = flow.streams[index].render(self._cid_mapper)
        if not self.compress:
            return raw, None
        return compressed_stream(raw), {N("Filter"): N("FlateDecode")}

    def _render_page_streams(self) -> None:
        """Pre-render and (in parallel) compress every page content stream.

        Phase 6: ``zlib.compress`` releases the GIL, so page streams are
        compressed concurrently on a small bounded worker pool
        (``min(os.cpu_count(), 8)``) and results are collected in page
        order, keeping the final bytes byte-for-byte identical to serial
        compression.  Each task renders its own raw stream (a pure read of
        the stream operators and the CID mapper) and compresses it, so no
        copy of all raw streams is kept at peak.  ``parallel_compress=False``
        (or a single page) falls back to the serial path.
        """
        self._compressed_pages = None
        if self._flow is None:
            return
        streams = self._flow.streams
        if not self.compress or len(streams) < 2 or not self.parallel_compress:
            return
        mapper = self._cid_mapper

        def task(index: int) -> bytes:
            return compressed_stream(streams[index].render(mapper))

        workers = min(os.cpu_count() or 1, 8)
        if workers < 2:
            self._compressed_pages = [task(index) for index in range(len(streams))]
            return
        with ThreadPoolExecutor(max_workers=workers) as executor:
            self._compressed_pages = list(executor.map(task, range(len(streams))))

    def font_face(
        self, family: str, *, bold: bool = False, italic: bool = False
    ) -> str:
        """Allocate (and return) the font resource name for a family+style.

        ``font_face("Helvetica")`` returns ``F1`` and ``("Helvetica",
        bold=True)`` returns ``F2``; new combinations get the next free
        ``F{N}`` resource name and are registered for use in the flow.
        """
        standard = standard_font_name(family, bold=bold, italic=italic)
        name = self._font_names.get(standard)
        if name is None:
            name = "F%d" % (len(self._font_names) + 1)
            self._font_names[standard] = name
            self._resource_fonts[name] = standard
        return name

    def font_ref(self, name: str) -> ObjectId:
        """Return (and lazily register) the font resource ``name``.

        Under embed mode the resource resolves through the font registry to a
        Liberation face and the five-object Type0/CIDFontType2 chain is
        reserved (bodies attach at render, after subsets are generated).
        Otherwise the phase-1 Type1 placeholder is used.
        """
        ref = self._font_refs.get(name)
        if ref is not None:
            return ref
        base_font = self._resource_fonts.get(name)
        if base_font is None:
            raise ValueError(f"unknown font resource {name!r}")
        if self._doc.mode_embed_fonts:
            entry = self._font_registry.entry(name, base_font)
            ref = FontChain(entry).reserve_thunks(self)
        else:
            font = Font(resource_name=name, base_font=base_font)
            ref = self._reserve_value(lambda f=font: f.to_dict())
        self._font_refs[name] = ref
        return ref

    def font_is_cid(self, name: str) -> bool:
        """True when ``name`` resolves to an embedded CID font."""
        return self._font_registry.font_is_cid(name)

    def record_font_usage(self, name: str, text: str) -> None:
        """Collect characters drawn with a font resource (for subsetting)."""
        self._font_registry.record_chars(name, text)

    def structure_manager(self) -> Optional[StructureManager]:
        """The document's structure manager (None when output is untagged)."""
        return self._structure

    def page_ref(self, index: int) -> ObjectId:
        """The object reference of page ``index`` (for ``/Pg`` on elements)."""
        return self._page_refs[index]

    # ------------------------------------------------------------------
    # Phase 7: outlines, links and form fields
    # ------------------------------------------------------------------

    def add_outline(
        self,
        title: str,
        *,
        page_index: int,
        y: Optional[float] = None,
        parent: Optional[OutlineItem] = None,
    ) -> OutlineItem:
        """Add an outline item (bookmark) jumping to ``page_index``.

        ``y`` is the destination top in top-down page coordinates (``None``
        means fit the page).  In tagged output the destination is a
        structure destination: a fresh ``/Sect`` element under the Document
        element is created and the item jumps to it (PDF/UA-2 clause 8.8
        requires every in-document destination to be a structure
        destination); untagged output points straight at the page.
        ``parent`` nests the item under a previously added item.
        """
        if self._outline_manager is None:
            raise ValueError("add_outline() requires outlines=True on DocumentBuilder")
        if page_index >= len(self._page_refs):
            raise ValueError(
                f"page {page_index} does not exist (document has "
                f"{len(self._page_refs)} page(s))"
            )
        pdf_y = self._page_size[1] - y if y is not None else None
        if self._structure is not None:
            sect = self._structure.create_element(
                "Sect", parent=self._structure.document_element(), page=page_index
            )
            target = sect.obj_ref
        else:
            target = self._page_refs[page_index]
        dest: List[Any] = (
            [target, N("XYZ"), 0, pdf_y, None]
            if pdf_y is not None
            else [target, N("Fit")]
        )
        return self._outline_manager.add_item(title, parent=parent, dest=dest)

    def _form_rect(
        self, x: float, y: float, width: float, height: float
    ) -> List[float]:
        """Convert a top-down field rect to PDF user-space ``[x0 y0 x1 y1]``."""
        top = self._page_size[1] - y
        return [x, top - height, x + width, top]

    def add_text_field(
        self,
        name: str,
        value: str,
        *,
        page_index: int,
        x: float,
        y: float,
        width: float,
        height: float,
        size: float = 10.0,
    ) -> FormField:
        """Add a text form field whose appearance shows ``value``.

        ``x``/``y`` are top-down page coordinates (matching the flow);
        the widget lands on the page's annotation list and in the catalog
        ``/AcroForm /Fields``.  Under tagged output the widget gets a
        ``/Form`` structure element; the field value's characters are
        added to the embedded font subset.
        """
        if self._form_manager is None:
            raise ValueError(
                "add_text_field() requires forms=True on DocumentBuilder"
            )
        if page_index >= len(self._page_refs):
            raise ValueError(
                f"page {page_index} does not exist (document has "
                f"{len(self._page_refs)} page(s))"
            )
        rect = self._form_rect(x, y, width, height)
        return self._form_manager.add_text_field(
            name, value, page_index=page_index, rect=rect, size=size
        )

    def add_checkbox(
        self,
        name: str,
        *,
        page_index: int,
        x: float,
        y: float,
        width: float,
        height: float,
        checked: bool = False,
    ) -> FormField:
        """Add a checkbox (``/FT /Btn``) with ``/Yes`` / ``/Off`` appearances."""
        if self._form_manager is None:
            raise ValueError("add_checkbox() requires forms=True on DocumentBuilder")
        if page_index >= len(self._page_refs):
            raise ValueError(
                f"page {page_index} does not exist (document has "
                f"{len(self._page_refs)} page(s))"
            )
        rect = self._form_rect(x, y, width, height)
        return self._form_manager.add_checkbox(
            name, page_index=page_index, rect=rect, checked=checked
        )

    def add_signature_field(
        self,
        name: str = "Signature1",
        *,
        page_index: int = 0,
        x: float,
        y: float,
        width: float,
        height: float,
        reason: Optional[str] = None,
        location: Optional[str] = None,
        contact_info: Optional[str] = None,
        signer_name: Optional[str] = None,
    ) -> FormField:
        """Add a digital signature field (``/FT /Sig``).

        The rendered document carries the byte-range placeholder and a
        zeroed ``/Contents``; run :func:`engine.signature.sign_pdf` on the
        rendered bytes with an RSA key to produce the signed file.  The
        meta entries (``reason``, ``location``, ``contact_info``,
        ``signer_name``) land in the signature dictionary; ``signer_name``
        is also the default CMS issuer CN unless ``sign_pdf`` overrides it.
        """
        if self._signature_manager is None:
            raise ValueError(
                "add_signature_field() requires signing=True on DocumentBuilder"
            )
        if page_index >= len(self._page_refs):
            raise ValueError(
                f"page {page_index} does not exist (document has "
                f"{len(self._page_refs)} page(s))"
            )
        rect = self._form_rect(x, y, width, height)
        return self._signature_manager.add_signature_field(
            name,
            page_index=page_index,
            rect=rect,
            reason=reason,
            location=location,
            contact_info=contact_info,
            signer_name=signer_name,
        )

    def _add_widget_annotation(self, page_index: int, annot_ref: ObjectId) -> None:
        """Attach a widget annotation to its page and enable tab order."""
        page = self._page_records[page_index]
        page.add_annotation(annot_ref)
        page.tabs = "S"

    def _form_font_ref(self) -> ObjectId:
        """The font object behind the form ``/DA`` resource (F1, embedded)."""
        self.font_ref("F1")
        return self._font_refs["F1"]

    def add_link_annotation(
        self, page_index: int, rect: Sequence[float], uri: str
    ) -> ObjectId:
        """Reserve a link annotation, attach it to the page and enable /Tabs.

        The annotation body attaches at render time; the page records it in
        ``/Annots`` and gets ``/Tabs /S`` (a page with annotations must be
        tab-ordered, per the UA-2 fixture expectations).
        """
        ref = self._reserve_value(lambda: link_annotation_dict(rect, uri))
        page = self._page_records[page_index]
        page.add_annotation(ref)
        page.tabs = "S"
        return ref

    def image_ref(self, data: bytes) -> str:
        """Register an image XObject, deduplicating identical decoded content.

        Returns the resource name (``Im1``, ``Im2``, ...) for the content
        stream's ``/ImN Do`` operator.  Two calls with byte-identical images
        share one XObject object.  Under PDF/A-4 mode the image's bare
        ``/DeviceRGB`` / ``/DeviceGray`` colour space is rewritten to the
        matching ``[/ICCBased ...]`` reference at attach time (filters
        unchanged).
        """
        info = self._image_cache.get(data)
        if info is None:
            info = parse_image(data)
            self._image_cache[data] = info
        stream_data, extra = info.xobject_stream()
        key = (info.kind, info.width, info.height, info.colorspace, stream_data)
        name = self._image_keys.get(key)
        if name is None:
            name = "Im%d" % (len(self._image_keys) + 1)
            if self._pdfa:
                extra = rewrite_image_colorspace(
                    extra, self._icc_srgb_ref, self._icc_gray_ref
                )
            ref = self._reserve_stream(lambda d=stream_data, e=extra: (d, e))
            self._xobject_refs[name] = ref
            self._image_keys[key] = name
        return name

    def svg_ref(
        self,
        svg: str,
        *,
        width: float,
        height: float,
        transform: Optional[str] = None,
        fill: Optional[str] = "#000000",
        stroke: Optional[str] = None,
        stroke_width: float = 1.0,
    ) -> str:
        """Register an SVG-path Form XObject, deduplicating identical shapes.

        Returns the resource name (``Im1``, ``Im2``, ...) for the content
        stream's ``/ImN Do`` operator.  Two calls with identical path data,
        dimensions and style share one XObject object.  Phase 7.6: pure
        Python SVG path parsing + cubic-Bezier arcs (engine.svg).
        """
        from .svg import svg_form_xobject

        key = (
            "svg", svg, width, height, transform, fill, stroke, stroke_width
        )
        name = self._image_keys.get(key)
        if name is None:
            name = "Im%d" % (len(self._image_keys) + 1)
            stream_data, extra = svg_form_xobject(
                svg,
                width=width,
                height=height,
                transform=transform,
                fill=fill,
                stroke=stroke,
                stroke_width=stroke_width,
            )
            ref = self._reserve_stream(lambda d=stream_data, e=extra: (d, e))
            self._xobject_refs[name] = ref
            self._image_keys[key] = name
        return name

    # ------------------------------------------------------------------
    # Final assembly
    # ------------------------------------------------------------------

    def _apply_encryption(self, data: bytes) -> bytes:
        """Post-process the rendered bytes when ``encrypt`` was configured.

        Phase 7.5: the /Standard security handler (engine.encrypt) runs
        over the fully rendered document -- strings become encrypted hex
        strings, streams are encrypted, the /Encrypt dictionary is
        appended and the trailer gains the /Encrypt reference.  Returns
        ``data`` unchanged when encryption is off (the default).
        """
        if self._encrypt is None:
            return data
        return encrypt_pdf(data, self._encrypt)

    def render(self) -> bytes:
        """Attach every reserved object in reserve order and emit the PDF bytes."""
        flow = self._flow
        if flow is None:
            raise ValueError("DocumentBuilder: call flow() before render()")
        self._font_registry.generate_subsets()
        self._cid_mapper = self._font_registry.cid_for
        self._render_page_streams()
        for index, page in enumerate(self._page_records):
            for name in sorted(flow.page_fonts[index]):
                page.add_font(name, self._font_refs[name])
            for name in sorted(flow.page_xobjects[index]):
                page.add_xobject(name, self._xobject_refs[name])
            if self._structure is not None:
                # /StructParents is the page's key in the ParentTree number
                # tree, not an MCID count; only pages with MCIDs get one.
                if self._structure.mcid_count(index):
                    page.struct_parents = index
        for ref, kind, thunk in self._reserved:
            if kind == "stream":
                data, extra = thunk()
                self._doc.set_stream(ref, data, extra)
            else:
                self._doc.set_value(ref, thunk())
        self._compressed_pages = None  # bodies are encoded; release the pool copy
        self._doc.set_root(self._catalog_ref)
        if not self._pdfa:
            self._doc.set_info(self._info_ref)
        return self._apply_encryption(self._doc.render())

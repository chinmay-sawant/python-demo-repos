"""AcroForm: the interactive form dictionary, widgets and appearances.

Phase 7.3 owns the catalog's ``/AcroForm`` entry: the form dictionary
(``/Fields``, ``/NeedAppearances false``, the ``/DA`` default appearance
string and the ``/DR`` default resources), the merged field/widget
annotations (``/Subtype /Widget`` with ``/FT``, ``/Rect``, ``/T``, ``/V``,
``/P``, ``/F 4``) and the minimal appearance streams viewers need to show
field values without regeneration.

Design decisions:

* **Merged field/widget**: each field dictionary *is* its widget
  annotation (allowed by ISO 32000-2 12.7.3), so one object serves both
  roles.
* **Appearance streams always emitted**: PDF/A-4 (ISO 19005-4:2020
  6.3.3) requires an ``/AP`` on every annotation; text widgets get one
  stream under ``/N``, button (checkbox) widgets get the ``/N``
  subdictionary with ``/Yes`` and ``/Off`` state streams.  With all
  appearances up to date, ``/NeedAppearances false`` is correct.
* **Text uses the document font**: the ``/DA`` string and the appearance
  stream both reference the builder's ``F1`` resource, which under
  embed/compliant modes is the embedded Liberation chain (A-4: fonts in
  appearances must be embedded, and the subset must cover the value).
  The value's characters are recorded for subsetting at add time.
* **Tagged output**: under PDF/UA-2 (clause 8.10.1) every widget shall be
  enclosed by a ``/Form`` structure element; the builder allocates one
  per widget through the structure manager (a ``/Form`` StructElem with an
  ``/OBJR`` pointing at the widget), and the widget's ``/StructParent``
  maps into the ParentTree so validators can resolve the enclosure.  The
  ``/Contents`` entry supplies the label text that PDF/UA-2 clause
  8.10.2.3 demands.
* **Untagged output**: no structure entries are emitted at all (no
  ``/StructParent``, no ``/Form`` elements).

Appearance streams never emit colour operators (the initial graphics
state draws black), so they need no ICC /DefaultRGB under A-4 and stay
valid in plain PDF 2.0.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .content import ContentStream
from .structure import StructureManager
from .write import B, N, ObjectId, PdfName, compressed_stream, format_number

__all__ = ["FormField", "FormManager"]

#: The resource name used in ``/DA`` strings and appearance resources.
#: The builder guarantees this maps to the document's (embedded) font.
_DA_FONT = "F1"

#: Text baseline inset inside the appearance stream's /BBox (points).
_AP_PAD = 2.0


class FormField:
    """One merged field/widget annotation plus its appearance stream refs.

    ``rect`` is in PDF user space (``[x0 y0 x1 y1]``, bottom-up, absolute
    page coordinates).  ``ap_refs`` maps appearance state names (``N`` for
    text fields, ``Yes``/``Off`` for checkboxes) to the reserved stream
    object references; ``struct_elem`` is the widget's ``/Form``
    Structure element when the document is tagged.
    """

    __slots__ = (
        "ap_refs",
        "ft",
        "name",
        "obj_ref",
        "page_index",
        "rect",
        "size",
        "struct_elem",
        "value",
    )

    def __init__(
        self,
        ft: str,
        name: str,
        value: str,
        page_index: int,
        rect: Sequence[float],
        size: float,
    ) -> None:
        self.ft = ft
        self.name = name
        self.value = value
        self.page_index = page_index
        self.rect = [float(v) for v in rect]
        self.size = size
        self.obj_ref: Optional[ObjectId] = None
        self.ap_refs: Dict[str, ObjectId] = {}
        self.struct_elem: Optional[Any] = None


class FormManager:
    """Per-document AcroForm bookkeeping: form dict, widgets, appearances.

    All objects are reserved at add time (widget first, then its
    appearance stream(s)); bodies attach at render time through the host's
    deferred queue.  ``page_count`` / ``cid_mapper`` / ``compress`` /
    ``font_ref`` are render-time getters so keys and streams are computed
    when the final page set and font subsets are known.
    """

    def __init__(
        self,
        *,
        reserve_value: Callable[[Callable[[], Any]], ObjectId],
        reserve_stream: Callable[[Callable[[], Tuple[bytes, Optional[Dict[Any, Any]]]]], ObjectId],
        page_ref: Callable[[int], ObjectId],
        annotate: Callable[[int, ObjectId], None],
        structure: Optional[StructureManager],
        page_count: Callable[[], int],
        font_ref: Callable[[], ObjectId],
        font_is_cid: Callable[[str], bool],
        record_chars: Callable[[str, str], None],
        cid_mapper: Callable[[], Optional[Callable[[str, str], int]]],
        compress: Callable[[], bool],
    ) -> None:
        self._reserve_value = reserve_value
        self._reserve_stream = reserve_stream
        self._page_ref = page_ref
        self._annotate = annotate
        self._structure = structure
        self._page_count = page_count
        self._font_ref = font_ref
        self._font_is_cid = font_is_cid
        self._record_chars = record_chars
        self._cid_mapper = cid_mapper
        self._compress = compress
        self._fields: List[FormField] = []
        self._acroform_ref = reserve_value(lambda: self._acroform_dict())

    @property
    def acroform_ref(self) -> ObjectId:
        """The reserved ``/AcroForm`` object reference (for the catalog)."""
        return self._acroform_ref

    @property
    def fields(self) -> List[FormField]:
        """All fields in creation order (mirrors ``/Fields``)."""
        return list(self._fields)

    # ------------------------------------------------------------------
    # Field creation (layout-time calls)
    # ------------------------------------------------------------------

    def add_text_field(
        self,
        name: str,
        value: str,
        *,
        page_index: int,
        rect: Sequence[float],
        size: float = 10.0,
    ) -> FormField:
        """Create a text field widget whose appearance shows ``value``.

        The value's characters are recorded against the document font so
        the embedded subset covers them; the appearance stream is rendered
        (as CID text when the font is a subset) at attach time.
        """
        self._font_ref()  # force font registration before usage
        self._record_chars(_DA_FONT, value)
        field = FormField("Tx", name, value, page_index, rect, size)
        field.obj_ref = self._reserve_value(lambda: self._widget_dict(field))
        ap_ref = self._reserve_stream(lambda: self._text_appearance_stream(field))
        field.ap_refs["N"] = ap_ref
        self._register(field)
        return field

    def add_checkbox(
        self,
        name: str,
        *,
        page_index: int,
        rect: Sequence[float],
        checked: bool = False,
    ) -> FormField:
        """Create a checkbox (``/FT /Btn``) with ``/Yes`` and ``/Off`` states.

        ``checked`` picks the value and appearance state; both states get
        an appearance stream so viewers never need to regenerate (and the
        PDF/A-4 ``/N`` subdictionary requirement is met).
        """
        field = FormField("Btn", name, "Yes" if checked else "Off", page_index, rect, 0.0)
        field.obj_ref = self._reserve_value(lambda: self._widget_dict(field))
        field.ap_refs["Yes"] = self._reserve_stream(
            lambda: self._btn_appearance_stream(field, checked=True)
        )
        field.ap_refs["Off"] = self._reserve_stream(
            lambda: self._btn_appearance_stream(field, checked=False)
        )
        self._register(field)
        return field

    def add_signature_field(
        self,
        name: str,
        sig_dict_ref: ObjectId,
        *,
        page_index: int,
        rect: Sequence[float],
    ) -> FormField:
        """Create a signature field (``/FT /Sig``) whose ``/V`` is ``sig_dict_ref``.

        Phase 7.4: the widget carries no ``/DA`` (signatures draw no value)
        and gets an empty appearance stream so PDF/A-4's annotation
        appearance rule stays satisfied; ``/V`` is an indirect reference to
        the signature dictionary built by
        :class:`engine.signature.SignatureManager`.
        """
        field = FormField("Sig", name, sig_dict_ref, page_index, rect, 0.0)
        field.obj_ref = self._reserve_value(lambda: self._widget_dict(field))
        field.ap_refs["N"] = self._reserve_stream(
            lambda: self._sig_appearance_stream(field)
        )
        self._register(field)
        return field

    def _register(self, field: FormField) -> None:
        """Wire structure (tagged) and the page annotation list."""
        if self._structure is not None:
            elem = self._structure.add_annotation_element(
                "Form",
                parent=self._structure.document_element(),
                page_index=field.page_index,
                annot_ref=field.obj_ref,
            )
            field.struct_elem = elem
        self._annotate(field.page_index, field.obj_ref)
        self._fields.append(field)

    # ------------------------------------------------------------------
    # Object bodies (evaluated at render time)
    # ------------------------------------------------------------------

    def _acroform_dict(self) -> Dict[PdfName, Any]:
        """The ``/AcroForm`` dictionary over every widget.

        ``/NeedAppearances false`` (appearances are up to date),
        ``/DA`` the default appearance string and ``/DR`` the default
        resources mapping the DA font to the document's (embedded) font.
        """
        return {
            N("Type"): N("AcroForm"),
            N("Fields"): [field.obj_ref for field in self._fields],
            N("NeedAppearances"): B(False),
            N("DA"): "/%s %s Tf 0 g" % (_DA_FONT, format_number(10.0)),
            N("DR"): {N("Font"): {N(_DA_FONT): self._font_ref()}},
        }

    def _widget_dict(self, field: FormField) -> Dict[PdfName, Any]:
        """The merged field/widget annotation dictionary for ``field``."""
        d: Dict[PdfName, Any] = {
            N("Type"): N("Annot"),
            N("Subtype"): N("Widget"),
            N("FT"): N(field.ft),
            N("Rect"): list(field.rect),
            N("F"): 4,  # Print flag (PDF/A-4 requires it on annotations)
            N("P"): self._page_ref(field.page_index),
            N("T"): field.name,
            N("V"): field.value,
            # Label text: PDF/UA-2 8.10.2.3 needs a label or Contents entry.
            N("Contents"): field.name,
        }
        if field.ft == "Tx":
            d[N("DA")] = "/%s %s Tf 0 g" % (_DA_FONT, format_number(field.size))
            d[N("AP")] = {N("N"): field.ap_refs["N"]}
        elif field.ft == "Sig":
            # /V stays an indirect reference to the signature dictionary.
            d[N("AP")] = {N("N"): field.ap_refs["N"]}
        else:
            d[N("AS")] = N(field.value)
            d[N("V")] = N(field.value)
            d[N("AP")] = {
                N("N"): {
                    N("Yes"): field.ap_refs["Yes"],
                    N("Off"): field.ap_refs["Off"],
                }
            }
        if self._structure is not None and field.struct_elem is not None:
            d[N("StructParent")] = self._structure.annotation_parent_key(
                field.struct_elem
            )
        return d

    def _ap_stream_dict(self, width: float, height: float) -> Dict[PdfName, Any]:
        """The Form XObject dict for an appearance stream (BBox + font resource)."""
        return {
            N("Type"): N("XObject"),
            N("Subtype"): N("Form"),
            N("BBox"): [0.0, 0.0, width, height],
            N("Resources"): {N("Font"): {N(_DA_FONT): self._font_ref()}},
        }

    def _text_appearance_stream(
        self, field: FormField
    ) -> Tuple[bytes, Optional[Dict[Any, Any]]]:
        """The ``/N`` appearance stream drawing the field's value text."""
        width = field.rect[2] - field.rect[0]
        height = field.rect[3] - field.rect[1]
        stream = ContentStream()
        stream.text_line(
            field.value,
            x=_AP_PAD,
            y=_AP_PAD,
            resource_name=_DA_FONT,
            size=field.size,
            cids=self._font_is_cid(_DA_FONT),
        )
        raw = stream.render(self._cid_mapper())
        extra: Dict[Any, Any] = self._ap_stream_dict(width, height)
        if self._compress():
            return compressed_stream(raw), {N("Filter"): N("FlateDecode"), **extra}
        return raw, extra

    def _btn_appearance_stream(
        self, field: FormField, *, checked: bool
    ) -> Tuple[bytes, Optional[Dict[Any, Any]]]:
        """One checkbox state appearance (filled box when ``checked``)."""
        width = field.rect[2] - field.rect[0]
        height = field.rect[3] - field.rect[1]
        stream = ContentStream()
        if checked:
            stream.rect(0.0, 0.0, width, height)
            stream.fill()
        else:
            stream.set_line_width(1.0)
            stream.rect(1.0, 1.0, width - 2.0, height - 2.0)
            stream.stroke()
        raw = stream.render()
        extra: Dict[Any, Any] = self._ap_stream_dict(width, height)
        if self._compress():
            return compressed_stream(raw), {N("Filter"): N("FlateDecode"), **extra}
        return raw, extra

    def _sig_appearance_stream(
        self, field: FormField
    ) -> Tuple[bytes, Optional[Dict[Any, Any]]]:
        """The empty ``/N`` appearance stream of a signature widget.

        PDF/A-4 (ISO 19005-4:2020 6.3.3) requires an appearance on every
        annotation; an empty Form XObject draws nothing, so the signature
        field stays invisible while satisfying the rule.  No font resource
        is needed (nothing is drawn), keeping the stream independent of
        the document's font subsets.
        """
        width = field.rect[2] - field.rect[0]
        height = field.rect[3] - field.rect[1]
        extra: Dict[Any, Any] = {
            N("Type"): N("XObject"),
            N("Subtype"): N("Form"),
            N("BBox"): [0.0, 0.0, width, height],
        }
        return b"", extra

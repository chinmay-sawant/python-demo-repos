"""PDF/UA-2 structure tree: MCID allocation, StructElems, ParentTree.

Phase 5 owns everything between the tagged content operators in a page
stream and the catalog's ``/StructTreeRoot``: per-page MCID counters, the
owning-element bookkeeping that feeds the ``/ParentTree`` number tree, the
``/StructElem`` objects (Document / H1 / P / Table / TR / TH / TD / Figure /
Link) with parent-before-children object IDs, the PDF 2.0 ``/Namespace``
object (``http://iso.org/pdf2/ssn``) and link annotations for ``/Link``
elements.

The :class:`StructureManager` is attached to a host that reserves object
IDs on its behalf (``reserve_value``) and maps page indexes to page object
references (``page_ref``).  All reservations happen at layout time so the
parent-before-children invariant falls out of creation order; the bodies
attach at render time through the host's deferred queue, matching the
phase-2 reserve-then-attach flow.

Phase 6 adds the structure hot path: the per-cell ``N("Type")`` /
``N("S")`` ... ``PdfName`` churn is replaced with module-level constants,
and leaf elements (no kids beyond owned MCIDs, no title/alt) take the
single-use fast path in :meth:`StructureManager.element_dict_fast` that
builds their dictionary without per-key allocation.

Phase 7 adds annotation enclosure: :meth:`StructureManager.add_annotation_element`
creates an element (e.g. ``/Form`` around a widget annotation) that owns
the annotation via an ``/OBJR`` kid, and the ParentTree maps the
annotation's ``/StructParent`` key to it (keys live above the per-page
MCID keys, so they never collide).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from .write import N, ObjectId, PdfName, RawPdfBody, encode_string

__all__ = [
    "PDF2_SSN_NAMESPACE",
    "StructElem",
    "StructureManager",
    "header_id_list",
    "link_annotation_dict",
]

#: The PDF 2.0 standard structure namespace (ISO 32000-2:2020, 14.7.3.2).
PDF2_SSN_NAMESPACE = "http://iso.org/pdf2/ssn"

# Module-level name constants: dense tables encode the same handful of
# StructElem keys tens of thousands of times, and PdfName construction per
# cell was a measurable fraction of render time.
_N_TYPE = N("Type")
_N_S = N("S")
_N_P = N("P")
_N_K = N("K")
_N_PG = N("Pg")
_N_A = N("A")
_N_O = N("O")
_N_TABLE = N("Table")
_N_SCOPE = N("Scope")
_N_HEADERS = N("Headers")
_N_STRUCTELEM = N("StructElem")

# Cached PdfName per element type (``TD``, ``TH``, ``TR``, ...) and table
# attribute tokens (``Column``, ...).
_ELEM_TYPE_NAMES: Dict[str, PdfName] = {}

# Reused /Headers lists for column indices 0..63 (HFT/dense tables).
_HEADER_ID_LISTS: List[List[str]] = [["H%d" % i] for i in range(64)]


def _elem_type_name(type_name: str) -> PdfName:
    """The cached ``PdfName`` for an element type (``"TD"`` -> ``/TD``)."""
    cached = _ELEM_TYPE_NAMES.get(type_name)
    if cached is None:
        cached = N(type_name)
        _ELEM_TYPE_NAMES[type_name] = cached
    return cached


def header_id_list(col: int) -> List[str]:
    """Cached ``[\"Hn\"]`` list for table column ``col`` (avoids per-cell alloc)."""
    if 0 <= col < len(_HEADER_ID_LISTS):
        return _HEADER_ID_LISTS[col]
    return ["H%d" % col]


class StructElem:
    """One ``/StructElem`` object: type, parent pointer, kids, page, extras.

    ``kids`` mixes integer MCIDs (owned content on ``page``), child
    :class:`StructElem` references and ``/OBJR`` dictionaries (for links).
    ``scope`` is the ``/Scope`` attribute (``Column``/``Row``/``Both``) and
    ``struct_id`` / ``headers`` are the ``/ID`` / ``/Headers`` attributes
    that tie table header cells (TH) to body cells (TD) per ISO 32000-2
    14.8.5.7.
    """

    __slots__ = (
        "alt",
        "headers",
        "kids",
        "obj_ref",
        "page",
        "parent",
        "scope",
        "struct_id",
        "title",
        "type_name",
    )

    def __init__(
        self,
        type_name: str,
        *,
        parent: Optional["StructElem"] = None,
        title: Optional[str] = None,
        alt: Optional[str] = None,
        page: Optional[int] = None,
        scope: Optional[str] = None,
        struct_id: Optional[str] = None,
        headers: Optional[Sequence[str]] = None,
    ) -> None:
        self.type_name = type_name
        self.parent = parent
        self.title = title
        self.alt = alt
        self.page = page
        self.scope = scope
        self.struct_id = struct_id
        # Dense tables pass a cached list from header_id_list(); keep it
        # without copying so encode can reuse the same /Headers object.
        if headers is None:
            self.headers = None
        elif type(headers) is list:
            self.headers = headers
        else:
            self.headers = list(headers)
        self.kids: List[Any] = []
        self.obj_ref: Optional[ObjectId] = None

    def add_mcid(self, mcid: int, page_index: int) -> None:
        """Own ``mcid`` on ``page_index``; all MCIDs of an element share one page."""
        if self.page is not None and self.page != page_index:
            raise ValueError(
                f"StructElem {self.type_name!r}: MCIDs must all live on one page "
                f"(page {self.page} vs {page_index})"
            )
        self.kids.append(mcid)
        self.page = page_index


class StructureManager:
    """Per-document structure bookkeeping for tagged output.

    Reserved object layout (in reserve order): the Namespace, StructTreeRoot
    and ParentTree objects, then the Document root element, then every
    element created while the flow runs.  The catalog is reserved after the
    manager so it can reference the StructTreeRoot.
    """

    def __init__(
        self,
        *,
        reserve_value: Callable[[Callable[[], Any]], ObjectId],
        page_ref: Callable[[int], ObjectId],
        page_count: Optional[Callable[[], int]] = None,
    ) -> None:
        self._reserve_value = reserve_value
        self._page_ref = page_ref
        self._page_count = page_count if page_count is not None else (lambda: 0)
        self._elements: List[StructElem] = []
        self._page_mcids: List[Dict[int, StructElem]] = []
        self._mcid_counts: List[int] = []
        self._annotation_elements: List[StructElem] = []
        self._namespace_ref = reserve_value(lambda: self._namespace_dict())
        self._tree_root_ref = reserve_value(lambda: self._tree_root_dict())
        self._parent_tree_ref = reserve_value(lambda: self._parent_tree_dict())
        self._root_elem = self.create_element("Document")

    # ------------------------------------------------------------------
    # References and the root element
    # ------------------------------------------------------------------

    @property
    def tree_root_ref(self) -> ObjectId:
        """The reserved StructTreeRoot object reference (for the catalog)."""
        return self._tree_root_ref

    @property
    def namespace_ref(self) -> ObjectId:
        """The reserved Namespace object reference."""
        return self._namespace_ref

    def document_element(self) -> StructElem:
        """The Document root element (parent of every top-level element)."""
        return self._root_elem

    # ------------------------------------------------------------------
    # Element and MCID bookkeeping (layout-time calls)
    # ------------------------------------------------------------------

    def create_element(
        self,
        type_name: str,
        *,
        parent: Optional[StructElem] = None,
        title: Optional[str] = None,
        alt: Optional[str] = None,
        page: Optional[int] = None,
        scope: Optional[str] = None,
        struct_id: Optional[str] = None,
        headers: Optional[Sequence[str]] = None,
    ) -> StructElem:
        """Create a StructElem, reserve its object ID and link it to ``parent``.

        The element's body is attached at render time through the host's
        deferred queue; its ID is reserved now, so parent elements always
        receive lower object numbers than their children.
        """
        elem = StructElem(
            type_name,
            parent=parent,
            title=title,
            alt=alt,
            page=page,
            scope=scope,
            struct_id=struct_id,
            headers=headers,
        )
        elem.obj_ref = self._reserve_value(lambda: self._element_dict(elem))
        if parent is not None:
            parent.kids.append(elem)
        self._elements.append(elem)
        return elem

    def begin_content(self, elem: StructElem, page_index: int) -> int:
        """Allocate the next MCID on ``page_index`` for ``elem``; return it.

        The caller wraps the corresponding content in ``BDC``/``EMC`` with
        this MCID; the manager records the ownership so the ParentTree can
        point every MCID back at its element.
        """
        while len(self._page_mcids) <= page_index:
            self._page_mcids.append({})
            self._mcid_counts.append(0)
        mcid = self._mcid_counts[page_index]
        self._mcid_counts[page_index] = mcid + 1
        self._page_mcids[page_index][mcid] = elem
        elem.add_mcid(mcid, page_index)
        return mcid

    def begin_cell(
        self,
        type_name: str,
        *,
        parent: StructElem,
        page_index: int,
        struct_id: Optional[str] = None,
        headers: Optional[Sequence[str]] = None,
        scope: Optional[str] = None,
    ) -> Tuple[StructElem, int]:
        """Create a cell StructElem and its MCID in one call (phase 6).

        The dense-table fast path for TH/TD cells: creates the element,
        reserves its object ID and allocates the MCID in one go, returning
        ``(elem, mcid)`` so the caller can emit the BDC operator with
        :meth:`ContentStream.begin_mcid`.  The per-page MCID->element map is
        keyed by ``(page_index, mcid)``-addressable dict as usual, so the
        ParentTree thunk stays unchanged.

        Hot path (dump structure-cell-heap): MCID is allocated up front and
        stored as the sole kid so we skip a second ``add_mcid`` pass and
        avoid growing empty kid lists on every dense-table cell.
        """
        while len(self._page_mcids) <= page_index:
            self._page_mcids.append({})
            self._mcid_counts.append(0)
        mcid = self._mcid_counts[page_index]
        self._mcid_counts[page_index] = mcid + 1

        elem = StructElem(
            type_name,
            parent=parent,
            page=page_index,
            scope=scope,
            struct_id=struct_id,
            headers=headers,
        )
        # Single-MCID cell: seed kids with the MCID (no later append / add_mcid).
        elem.kids = [mcid]
        elem.obj_ref = self._reserve_value(lambda e=elem: self._element_dict(e))
        parent.kids.append(elem)
        self._elements.append(elem)
        self._page_mcids[page_index][mcid] = elem
        return elem, mcid

    def mcid_count(self, page_index: int) -> int:
        """The number of MCIDs allocated on ``page_index`` (0 when none)."""
        if page_index >= len(self._mcid_counts):
            return 0
        return self._mcid_counts[page_index]

    def add_objr(
        self, elem: StructElem, annot_ref: ObjectId, page_ref: ObjectId
    ) -> None:
        """Attach a ``/Link`` element's OBJR (its annotation) to its kids."""
        elem.kids.append(
            {N("Type"): N("OBJR"), N("Obj"): annot_ref, N("Pg"): page_ref}
        )

    def add_annotation_element(
        self,
        type_name: str,
        *,
        parent: StructElem,
        page_index: int,
        annot_ref: ObjectId,
    ) -> StructElem:
        """Create a StructElem that encloses one annotation (phase 7).

        Used for ``/Form`` elements around widget annotations: the element
        owns the annotation through an ``/OBJR`` kid and is registered as
        an annotation element so the ParentTree gets a ``/StructParent``
        entry for it (the annotation's own ``/StructParent`` resolves
        directly to this element).
        """
        elem = self.create_element(type_name, parent=parent, page=page_index)
        self.add_objr(elem, annot_ref, self._page_ref(page_index))
        self._annotation_elements.append(elem)
        return elem

    def annotation_parent_key(self, elem: StructElem) -> int:
        """The ``/StructParent`` value for an annotation element.

        Annotation keys live above the per-page MCID keys (which occupy
        ``0 .. page_count-1``), so they can never collide; both the widget
        annotation and the ParentTree evaluate this at render time, when
        the final page count is known.
        """
        return self._page_count() + self._annotation_elements.index(elem)

    # ------------------------------------------------------------------
    # Object bodies (evaluated at render time)
    # ------------------------------------------------------------------

    def _namespace_dict(self) -> Dict[PdfName, Any]:
        """The PDF 2.0 Namespace object declaring the standard structure set."""
        return {N("Type"): N("Namespace"), N("NS"): PDF2_SSN_NAMESPACE}

    def _parent_tree_dict(self) -> Dict[PdfName, Any]:
        """The ParentTree number tree: page index -> MCID-indexed element refs.

        Only pages that actually carry MCIDs appear; the array position is
        the MCID, so ``/Nums [ 0 [ e0 e1 ] 2 [ e0 ] ]`` says page 0's MCID 0
        belongs to ``e0``, MCID 1 to ``e1`` and page 2's MCID 0 to ``e0``.

        Annotation elements (phase 7) follow the page entries with their
        own keys (``page_count + index``), each mapping one ``/StructParent``
        value to the enclosing element (e.g. a ``/Form`` around a widget).
        """
        nums: List[Any] = []
        for page_index, mcid_map in enumerate(self._page_mcids):
            if not mcid_map:
                continue
            nums.append(page_index)
            nums.append([mcid_map[mcid].obj_ref for mcid in range(len(mcid_map))])
        base = self._page_count()
        for index, elem in enumerate(self._annotation_elements):
            nums.append(base + index)
            nums.append(elem.obj_ref)
        return {N("Nums"): nums}

    def _tree_root_dict(self) -> Dict[PdfName, Any]:
        """The StructTreeRoot dictionary over the Document element."""
        return {
            N("Type"): N("StructTreeRoot"),
            N("K"): self._root_elem.obj_ref,
            N("ParentTree"): self._parent_tree_ref,
            N("Namespaces"): [self._namespace_ref],
        }

    def _element_dict(self, elem: StructElem) -> Dict[PdfName, Any]:
        """The StructElem dictionary for ``elem`` (rendered at attach time).

        Dense table cells (TD/TH with optional /Headers /Scope /ID and MCID
        kids only) take :meth:`element_dict_fast`.  Everything else (Document
        root, mixed kids, titles/alts, OBJR) builds the full dictionary.
        """
        if self._is_fast_shape(elem):
            return self.element_dict_fast(elem)
        d: Dict[PdfName, Any] = {
            _N_TYPE: _N_STRUCTELEM,
            _N_S: _elem_type_name(elem.type_name),
        }
        if elem is self._root_elem:
            d[_N_P] = self._tree_root_ref
        elif elem.parent is not None:
            d[_N_P] = elem.parent.obj_ref
        if elem.kids:
            kids: List[Any] = []
            for kid in elem.kids:
                if isinstance(kid, StructElem):
                    kids.append(kid.obj_ref)
                else:
                    kids.append(kid)
            d[_N_K] = kids
        if elem.page is not None:
            d[_N_PG] = self._page_ref(elem.page)
        if elem is self._root_elem:
            d[N("NS")] = self._namespace_ref
        if elem.title is not None:
            d[N("T")] = elem.title
        if elem.alt is not None:
            d[N("Alt")] = elem.alt
        if elem.struct_id is not None:
            d[N("ID")] = elem.struct_id
        # Table attributes (/Scope, /Headers) live in the /A attribute array
        # with owner /Table (ISO 32000-2 14.7.6.1); validators read them
        # from there, not as direct element keys.
        attribute: Dict[PdfName, Any] = {}
        if elem.scope is not None:
            attribute[_N_SCOPE] = N(elem.scope)
        if elem.headers:
            attribute[_N_HEADERS] = list(elem.headers)
        if attribute:
            attribute[_N_O] = _N_TABLE
            d[_N_A] = [attribute]
        return d

    def _is_fast_shape(self, elem: StructElem) -> bool:
        """True when ``elem`` can use :meth:`element_dict_fast`.

        Includes plain leaves, TR with element-ref kids, **and** table cells
        that only add /Headers /Scope /ID (the HFT/dense path).  Previously
        every TD with /Headers fell through to the slow builder — the dump
        hot spot for structure encode on tagged trade grids.
        """
        if elem is self._root_elem or elem.title is not None or elem.alt is not None:
            return False
        for kid in elem.kids:
            if isinstance(kid, dict):
                return False
        return True

    def element_dict_fast(self, elem: StructElem) -> Any:
        """Fast StructElem body for dense cells / TR / plain leaves.

        Single-MCID table cells (the HFT/dense TD/TH shape) return a
        :class:`RawPdfBody` of pre-encoded dictionary bytes — skipping
        recursive encode_dict/encode_value entirely.  Other fast shapes
        still return a normal dict.
        """
        kids = elem.kids
        # Dense TD/TH: exactly one integer MCID kid + parent + page.
        if (
            len(kids) == 1
            and type(kids[0]) is int
            and elem.parent is not None
            and elem.parent.obj_ref is not None
            and elem.page is not None
            and elem.title is None
            and elem.alt is None
        ):
            return self._raw_table_cell_body(elem, kids[0])

        d: Dict[PdfName, Any] = {
            _N_TYPE: _N_STRUCTELEM,
            _N_S: _elem_type_name(elem.type_name),
        }
        if elem.parent is not None:
            d[_N_P] = elem.parent.obj_ref
        if kids:
            first = kids[0]
            if type(first) is int:
                d[_N_K] = kids
            elif isinstance(first, StructElem):
                d[_N_K] = [kid.obj_ref for kid in kids]
            else:
                d[_N_K] = [
                    kid.obj_ref if isinstance(kid, StructElem) else kid
                    for kid in kids
                ]
        if elem.page is not None:
            d[_N_PG] = self._page_ref(elem.page)
        if elem.struct_id is not None:
            d[N("ID")] = elem.struct_id
        if elem.scope is not None or elem.headers:
            attribute: Dict[PdfName, Any] = {_N_O: _N_TABLE}
            if elem.scope is not None:
                attribute[_N_SCOPE] = _elem_type_name(elem.scope)
            if elem.headers:
                attribute[_N_HEADERS] = elem.headers
            d[_N_A] = [attribute]
        return d

    def _raw_table_cell_body(self, elem: StructElem, mcid: int) -> RawPdfBody:
        """Pre-encoded ``<< /Type /StructElem … >>`` for one table cell.

        Specialized TD/TH templates avoid per-cell bytearray churn on the
        HFT path (12k+ cells).  Spacing matches the generic encode_dict form
        closely enough for valid PDF; structure semantics are unchanged.
        """
        parent_ref = elem.parent.obj_ref.render_ref()
        page_ref = self._page_ref(elem.page).render_ref()
        mcid_b = b"%d" % mcid
        # Dominant shape: TD + single /Headers entry, no /ID /Scope.
        if (
            elem.type_name == "TD"
            and elem.headers
            and len(elem.headers) == 1
            and elem.scope is None
            and elem.struct_id is None
        ):
            return RawPdfBody(
                b"<< /Type /StructElem /S /TD /P "
                + parent_ref
                + b" /K ["
                + mcid_b
                + b"] /Pg "
                + page_ref
                + b" /A [<< /O /Table /Headers ["
                + encode_string(elem.headers[0])
                + b"] >>] >>"
            )
        # TH with /ID + /Scope=Column (header row).
        if (
            elem.type_name == "TH"
            and elem.struct_id is not None
            and elem.scope == "Column"
            and not elem.headers
        ):
            return RawPdfBody(
                b"<< /Type /StructElem /S /TH /P "
                + parent_ref
                + b" /K ["
                + mcid_b
                + b"] /Pg "
                + page_ref
                + b" /ID "
                + encode_string(elem.struct_id)
                + b" /A [<< /O /Table /Scope /Column >>] >>"
            )
        # Generic fast fallback (still no recursive encode_dict).
        from .write import encode_name as _enc_name

        body = bytearray(b"<< /Type /StructElem /S ")
        body.extend(_enc_name(_elem_type_name(elem.type_name)))
        body.extend(b" /P ")
        body.extend(parent_ref)
        body.extend(b" /K [")
        body.extend(mcid_b)
        body.extend(b"] /Pg ")
        body.extend(page_ref)
        if elem.struct_id is not None:
            body.extend(b" /ID ")
            body.extend(encode_string(elem.struct_id))
        if elem.scope is not None or elem.headers:
            body.extend(b" /A [<< /O /Table")
            if elem.scope is not None:
                body.extend(b" /Scope ")
                body.extend(_enc_name(elem.scope))
            if elem.headers:
                body.extend(b" /Headers [")
                first = True
                for header in elem.headers:
                    if not first:
                        body.extend(b" ")
                    body.extend(encode_string(header))
                    first = False
                body.extend(b"]")
            body.extend(b" >>]")
        body.extend(b" >>")
        return RawPdfBody(bytes(body))

def link_annotation_dict(
    rect: Sequence[float], uri: str
) -> Dict[PdfName, Any]:
    """A link annotation dictionary (``/Subtype /Link`` with a URI action).

    ``/F 4`` (the Print flag) is required by PDF/A-4 (ISO 19005-4:2020,
    7.4.3.6.2) for every annotation.
    """
    return {
        N("Type"): N("Annot"),
        N("Subtype"): N("Link"),
        N("Rect"): [float(v) for v in rect],
        N("Border"): [0, 0, 0],
        N("F"): 4,
        N("A"): {N("S"): N("URI"), N("URI"): uri},
    }

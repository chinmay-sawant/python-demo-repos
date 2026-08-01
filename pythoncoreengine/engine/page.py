"""Pages tree, page objects and placeholder font dictionaries.

Phase 1 owns ``/Type /Pages``, ``/Type /Page`` (resources, media box,
contents reference) and a simple Type1 standard font dict.  Phase 2 adds
per-page XObject resources (images) alongside fonts; the pages tree already
supports any number of kids.  Phase 5 adds the tagging page entries
(``/StructParents`` when the page carries MCIDs, ``/Tabs /S`` and
``/Annots`` for pages with link annotations); all three stay absent on
untagged pages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .write import N, ObjectId, PdfName

__all__ = [
    "A4_POINTS",
    "Font",
    "Page",
    "PagesTree",
    "DEFAULT_PAGE_SIZE",
]

# A4 portrait size in points (1 point = 1/72 inch).
A4_POINTS: tuple = (595.276, 841.89)
DEFAULT_PAGE_SIZE = A4_POINTS


class Font:
    """Placeholder simple Type1 standard font (e.g. Helvetica).

    No embedding until phase 3; the resource name is the key the page
    ``/Resources /Font`` map uses to expose this font to content streams.
    """

    def __init__(self, resource_name: str = "F1", base_font: str = "Helvetica") -> None:
        self.resource_name = resource_name
        self.base_font = base_font

    def to_dict(self) -> Dict[PdfName, Any]:
        """Return the font dictionary for a standard Type1 font."""
        return {
            N("Type"): N("Font"),
            N("Subtype"): N("Type1"),
            N("BaseFont"): N(self.base_font),
            N("Encoding"): N("WinAnsiEncoding"),
        }


class Page:
    """A single page object with per-page font and image resources."""

    def __init__(
        self,
        parent: ObjectId,
        contents: ObjectId,
        *,
        width: float = DEFAULT_PAGE_SIZE[0],
        height: float = DEFAULT_PAGE_SIZE[1],
        fonts: Optional[Dict[str, ObjectId]] = None,
        xobjects: Optional[Dict[str, ObjectId]] = None,
        color_spaces: Optional[Dict[PdfName, Any]] = None,
        struct_parents: Optional[int] = None,
        tabs: Optional[str] = None,
    ) -> None:
        self._parent = parent
        self._contents = contents
        self._width = width
        self._height = height
        self._fonts: Dict[PdfName, ObjectId] = {
            N(resource_name): font_ref for resource_name, font_ref in (fonts or {}).items()
        }
        self._xobjects: Dict[PdfName, ObjectId] = {
            N(resource_name): xobject_ref
            for resource_name, xobject_ref in (xobjects or {}).items()
        }
        self._color_spaces: Optional[Dict[PdfName, Any]] = color_spaces
        self._struct_parents: Optional[int] = struct_parents
        self._tabs: Optional[str] = tabs
        self._annotations: List[ObjectId] = []

    @property
    def struct_parents(self) -> Optional[int]:
        """The page's ``/StructParents`` count (None when the page has no MCIDs)."""
        return self._struct_parents

    @struct_parents.setter
    def struct_parents(self, value: Optional[int]) -> None:
        self._struct_parents = value

    @property
    def tabs(self) -> Optional[str]:
        """The page's ``/Tabs`` value (``S`` when the page has annotations)."""
        return self._tabs

    @tabs.setter
    def tabs(self, value: Optional[str]) -> None:
        self._tabs = value

    def add_font(self, resource_name: str, font_ref: ObjectId) -> None:
        """Expose a font resource to this page's content stream."""
        self._fonts[N(resource_name)] = font_ref

    def add_xobject(self, resource_name: str, xobject_ref: ObjectId) -> None:
        """Expose an image XObject resource to this page's content stream."""
        self._xobjects[N(resource_name)] = xobject_ref

    def add_annotation(self, annot_ref: ObjectId) -> None:
        """Add a link annotation to the page's ``/Annots`` array."""
        self._annotations.append(annot_ref)

    def to_dict(self) -> Dict[PdfName, Any]:
        """Return the page dictionary with parent, media box, contents and resources.

        ``/XObject`` is only present when the page actually references image
        resources, and ``/ColorSpace`` only when ``color_spaces`` was given
        (PDF/A-4 mode).  ``/StructParents``, ``/Annots`` and ``/Tabs`` are
        emitted only when tagging or annotations are in play, keeping
        phase-1/2 output byte-identical.
        """
        resources: Dict[PdfName, Any] = {N("Font"): self._fonts}
        if self._xobjects:
            resources[N("XObject")] = self._xobjects
        if self._color_spaces:
            resources[N("ColorSpace")] = self._color_spaces
        page: Dict[PdfName, Any] = {
            N("Type"): N("Page"),
            N("Parent"): self._parent,
            N("MediaBox"): [0, 0, self._width, self._height],
            N("Contents"): self._contents,
            N("Resources"): resources,
        }
        if self._struct_parents is not None:
            page[N("StructParents")] = self._struct_parents
        if self._annotations:
            page[N("Annots")] = list(self._annotations)
        if self._tabs is not None:
            page[N("Tabs")] = N(self._tabs)
        return page


class PagesTree:
    """The document pages tree (``/Type /Pages``); kids grow with each page."""

    def __init__(self, kids: Optional[List[ObjectId]] = None) -> None:
        self._kids: List[ObjectId] = list(kids or [])

    def add_page(self, page_ref: ObjectId) -> "PagesTree":
        """Append a page reference and return self for chaining."""
        self._kids.append(page_ref)
        return self

    @property
    def count(self) -> int:
        """The number of page kids (mirrors ``/Count``)."""
        return len(self._kids)

    def to_dict(self) -> Dict[PdfName, Any]:
        """Return the pages-tree dictionary with kids and a correct /Count."""
        return {
            N("Type"): N("Pages"),
            N("Kids"): list(self._kids),
            N("Count"): len(self._kids),
        }

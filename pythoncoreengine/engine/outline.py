"""Outline tree (bookmarks): the ``/Outlines`` dictionary and its items.

Phase 7.1 owns the catalog-level navigation tree: one ``/Outlines``
dictionary object whose ``/First`` / ``/Last`` / ``/Count`` point at the
top-level items, and one object per item carrying ``/Title``, ``/Parent``,
``/Next`` / ``/Prev`` (sibling wiring), ``/First`` / ``/Last`` / ``/Count``
(child wiring) and a ``/Dest`` destination.

Destinations are built eagerly by the caller and passed in, so this module
stays free of page and structure knowledge: plain PDF 2.0 output uses page
destinations (``[page /Fit]`` or ``[page /XYZ left top null]``), while
tagged output uses structure destinations (``[sect /XYZ ...]``) so the
PDF/UA-2 rule (ISO 14289-2:2024 clause 8.8: every in-document destination
shall be a structure destination) stays satisfied.  The document builder
decides which form to use when it wires the manager.

All objects are reserved at add time and their bodies attach at render
time through the host's deferred queue, matching the phase-2
reserve-then-attach flow: the ``/Outlines`` dictionary is reserved first
(so the catalog can reference it), then one object per item in creation
order.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .write import N, ObjectId, PdfName

__all__ = ["OutlineItem", "OutlineManager"]


class OutlineItem:
    """One outline item: title, destination and its tree links.

    ``dest`` is an eagerly built PDF destination array (for example
    ``[page_ref, /Fit]``); the tree links (``/Parent`` / ``/Next`` /
    ``/Prev`` / ``/First`` / ``/Last`` / ``/Count``) are derived from
    ``parent`` / ``children`` at render time.  ``obj_ref`` is reserved by
    the manager at creation time.
    """

    __slots__ = ("children", "dest", "obj_ref", "parent", "title")

    def __init__(
        self,
        title: str,
        *,
        parent: Optional["OutlineItem"],
        dest: List[Any],
    ) -> None:
        self.title = title
        self.parent = parent
        self.dest = list(dest)
        self.children: List[OutlineItem] = []
        self.obj_ref: Optional[ObjectId] = None


class OutlineManager:
    """Per-document outline tree: the ``/Outlines`` dict plus every item.

    ``reserve_value`` is the host's deferred-queue reservation callback;
    bodies attach at render time in reserve order.  Items are added after
    their destination page exists (``page_refs`` is consulted by the
    caller when building the eager destination), so the manager itself
    never touches page bookkeeping.
    """

    def __init__(
        self, reserve_value: Callable[[Callable[[], Any]], ObjectId]
    ) -> None:
        self._reserve_value = reserve_value
        self._roots: List[OutlineItem] = []
        self._items: List[OutlineItem] = []
        self._outlines_ref = reserve_value(lambda: self._outlines_dict())

    @property
    def outlines_ref(self) -> ObjectId:
        """The reserved ``/Outlines`` object reference (for the catalog)."""
        return self._outlines_ref

    @property
    def count(self) -> int:
        """The total number of outline items (all levels)."""
        return len(self._items)

    def add_item(
        self, title: str, *, parent: Optional[OutlineItem], dest: List[Any]
    ) -> OutlineItem:
        """Create an outline item under ``parent`` (or the root level).

        ``dest`` is the destination array built by the caller (already
        resolving page refs / structure elements).  The item's object ID
        is reserved now; the dictionary body attaches at render time.
        """
        item = OutlineItem(title, parent=parent, dest=dest)
        item.obj_ref = self._reserve_value(lambda: self._item_dict(item))
        if parent is None:
            self._roots.append(item)
        else:
            parent.children.append(item)
        self._items.append(item)
        return item

    # ------------------------------------------------------------------
    # Object bodies (evaluated at render time)
    # ------------------------------------------------------------------

    def _outlines_dict(self) -> Dict[PdfName, Any]:
        """The ``/Outlines`` dictionary over the top-level items.

        ``/First`` / ``/Last`` name the first and last root items and
        ``/Count`` the number of top-level items (ISO 32000-2, Table 152).
        """
        d: Dict[PdfName, Any] = {N("Type"): N("Outlines")}
        if self._roots:
            d[N("First")] = self._roots[0].obj_ref
            d[N("Last")] = self._roots[-1].obj_ref
        d[N("Count")] = len(self._roots)
        return d

    def _item_dict(self, item: OutlineItem) -> Dict[PdfName, Any]:
        """The outline item dictionary with tree links and the destination.

        ``/Parent`` is the enclosing item (or the ``/Outlines`` dict for
        top-level items); ``/Prev`` / ``/Next`` link the sibling chain and
        ``/First`` / ``/Last`` / ``/Count`` the children (ISO 32000-2,
        Table 153).
        """
        siblings = item.parent.children if item.parent is not None else self._roots
        index = siblings.index(item)
        d: Dict[PdfName, Any] = {
            N("Title"): item.title,
            N("Parent"): (
                item.parent.obj_ref if item.parent is not None else self._outlines_ref
            ),
        }
        if index > 0:
            d[N("Prev")] = siblings[index - 1].obj_ref
        if index < len(siblings) - 1:
            d[N("Next")] = siblings[index + 1].obj_ref
        if item.children:
            d[N("First")] = item.children[0].obj_ref
            d[N("Last")] = item.children[-1].obj_ref
            d[N("Count")] = len(item.children)
        d[N("Dest")] = list(item.dest)
        return d

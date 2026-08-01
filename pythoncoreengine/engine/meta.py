"""XMP metadata packet builder for PDF/A-4 and PDF/UA-2 (stdlib only, deterministic).

Phase 4 requires every PDF/A-4 document to carry an XMP metadata stream
whose ``pdfaid`` schema declares part 4 / revision 2020.  Phase 5 adds the
PDF/UA-2 identification (``pdfuaid:part 2`` / ``pdfuaid:rev 2024``) and the
``pdfaExtension`` schema registration that declares the pdfuaid namespace
for PDF/A consumers.  :func:`build_xmp_packet` assembles the packet bytes --
header processing instruction with BOM and the fixed Adobe packet ID, the
``rdf:RDF`` body, whitespace padding and the closing ``<?xpacket
end="w"?>`` -- with all dates and UUIDs derived deterministically from the
caller's values.

Packet layout::

    <?xpacket begin="<BOM>" id="W5M0MpCehiHzreSzNTczkc9d"?>
    <?xml version="1.0" encoding="UTF-8"?>
    <x:xmpmeta xmlns:x="adobe:ns:meta/">
     <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
        xmlns:xmp="..." xmlns:dc="..." xmlns:pdf="..."
        xmlns:pdfaid="..." xmlns:xmpMM="..."
        [xmlns:pdfuaid="..." xmlns:pdfaExtension="..."]>
       <pdfaid:part>4</pdfaid:part>
       <pdfaid:rev>2020</pdfaid:rev>
       [<pdfuaid:part>2</pdfuaid:part>
        <pdfuaid:rev>2024</pdfuaid:rev>]
       ... dates, producer, creator tool, dc:format, document IDs ...
       [<pdfaExtension:schemas> ... registration of the pdfuaid namespace ... ]
      </rdf:Description>
     </rdf:RDF>
    </x:xmpmeta>
    <padding to >= 2048 bytes>
    <?xpacket end="w"?>

The packet is padded with whitespace so its total size is at least the
XMP-recommended 2048 bytes (rounded up to a 4-byte multiple), keeping
in-place metadata edits possible and matching what producers emit.

Phase 6: the static prefix and suffix of the packet (header PI, namespace
declarations, closing tags) are cached per ``pdfuaid`` presence, so a
dense document build only formats the variable middle (the property
values) instead of re-assembling the whole template.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from typing import Dict, Optional, Tuple

__all__ = [
    "XMP_PACKET_ID",
    "XMP_PACKET_MIN_SIZE",
    "build_xmp_dict",
    "build_xmp_packet",
]

# The fixed packet ID mandated by the XMP specification (Adobe packet ID).
XMP_PACKET_ID = "W5M0MpCehiHzreSzNTczkc9d"

# Recommended minimum packet size, in bytes.
XMP_PACKET_MIN_SIZE = 2048

_NS_X = "adobe:ns:meta/"
_NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_NS_XMP = "http://ns.adobe.com/xap/1.0/"
_NS_DC = "http://purl.org/dc/elements/1.1/"
_NS_PDF = "http://ns.adobe.com/pdf/1.3/"
_NS_PDFAID = "http://www.aiim.org/pdfa/ns/id/"
_NS_PDFUAID = "http://www.aiim.org/pdfua/ns/id/"
_NS_PDFA_EXTENSION = "http://www.aiim.org/pdfa/ns/extension/"
_NS_XMPMM = "http://ns.adobe.com/xap/1.0/mm/"

_ISO_8601 = "%Y-%m-%dT%H:%M:%SZ"

# A dc:title value as an rdf:Alt with an x-default item: the XMP property
# model expects dc:title to be a language-alternative array, and UA-2
# validators read the x-default item back.
def _lang_alt(value: str) -> str:
    """Wrap ``value`` as ``<rdf:Alt><rdf:li xml:lang="x-default">...</rdf:li></rdf:Alt>``."""
    return (
        "<rdf:Alt>\n"
        '    <rdf:li xml:lang="x-default">' + value + "</rdf:li>\n"
        "   </rdf:Alt>"
    )

# The pdfaExtension schema block registering the pdfuaid namespace (PDF/A-4
# requires any non-standard XMP namespace used in the packet to be declared
# through the extension schema mechanism; see ISO 19005-4:2020, 6.7.3.2).
_PDFA_EXTENSION_SCHEMAS = (
    '   <pdfaExtension:schemas>\n'
    '    <rdf:Bag>\n'
    '     <rdf:li rdf:parseType="Resource">\n'
    '      <pdfaExtension:namespaceURI>{ns}</pdfaExtension:namespaceURI>\n'
    '      <pdfaExtension:prefix>pdfuaid</pdfaExtension:prefix>\n'
    '      <pdfaExtension:property>\n'
    '       <rdf:Seq>\n'
    '        <rdf:li rdf:parseType="Resource">\n'
    '         <pdfaExtension:name>part</pdfaExtension:name>\n'
    '         <pdfaExtension:valueType>Integer</pdfaExtension:valueType>\n'
    '        </rdf:li>\n'
    '        <rdf:li rdf:parseType="Resource">\n'
    '         <pdfaExtension:name>rev</pdfaExtension:name>\n'
    '         <pdfaExtension:valueType>Integer</pdfaExtension:valueType>\n'
    '        </rdf:li>\n'
    '       </rdf:Seq>\n'
    '      </pdfaExtension:property>\n'
    '     </rdf:li>\n'
    '    </rdf:Bag>\n'
    '   </pdfaExtension:schemas>\n'
).format(ns=_NS_PDFUAID)


def _iso8601(when: datetime.datetime) -> str:
    """Format ``when`` as ISO 8601 UTC: ``YYYY-MM-DDTHH:MM:SSZ``.

    PDF/A-4 (and XMP generally) requires a timezone on every date; the
    naive timestamp passed by the builders is treated as UTC.
    """
    return when.strftime(_ISO_8601)


def _uuid_from_seed(*parts: str) -> str:
    """A deterministic ``uuid:...`` string derived from ``parts``."""
    digest = hashlib.md5("|".join(parts).encode("utf-8")).digest()
    return "uuid:" + str(uuid.UUID(bytes=digest))


def build_xmp_dict(
    *,
    created: datetime.datetime,
    producer: str,
    creator_tool: str = "pythoncoreengine",
    title: Optional[str] = None,
    creator: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    pdfaid_part: Optional[int] = 4,
    pdfaid_rev: Optional[int] = 2020,
    pdfuaid_part: Optional[int] = None,
    pdfuaid_rev: Optional[int] = None,
) -> Dict[str, str]:
    """The XMP property values as a plain {property: value} map.

    Used by :func:`build_xmp_packet` and available for tests.  Dates are
    ISO 8601, ``DocumentID``/``InstanceID`` are deterministic ``uuid:...``
    strings derived from ``created`` and ``producer`` (two runs with the
    same inputs produce identical packets).  The PDF/A identification
    (``pdfaid``) is emitted by default; pass ``pdfaid_part=None`` to omit it
    (a PDF/UA-2-only document).  Passing ``pdfuaid_part`` adds the PDF/UA-2
    identification (``pdfuaid`` prefix, namespace
    ``http://www.aiim.org/pdfua/ns/id/``).
    """
    iso = _iso8601(created)
    values: Dict[str, str] = {
        "xmp:CreateDate": iso,
        "xmp:ModifyDate": iso,
        "xmp:MetadataDate": iso,
        "xmp:CreatorTool": creator_tool,
        "pdf:Producer": producer,
        "dc:format": "application/pdf",
        "xmpMM:DocumentID": _uuid_from_seed(producer, iso, "document"),
        "xmpMM:InstanceID": _uuid_from_seed(producer, iso, "instance"),
    }
    if pdfaid_part is not None:
        values["pdfaid:part"] = str(pdfaid_part)
        values["pdfaid:rev"] = str(pdfaid_rev)
    if pdfuaid_part is not None:
        values["pdfuaid:part"] = str(pdfuaid_part)
        values["pdfuaid:rev"] = str(pdfuaid_rev)
    if title is not None:
        values["dc:title"] = _lang_alt(title)
    if creator is not None:
        values["dc:creator"] = creator
    if subject is not None:
        values["dc:subject"] = subject
    if description is not None:
        values["dc:description"] = description
    return values


# Cached packet prefix, keyed by ``(pdfuaid present, creator tool)`` (phase 6).
# The prefix runs through ``<rdf:Description rdf:about="">`` and the suffix
# from the closing tags onward; only the property body and the whitespace
# padding are built per call.
_XMP_PREFIX_CACHE: Dict[Tuple[bool, str], str] = {}
_XMP_SUFFIX = (
    "\n  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
)


def _xmp_prefix(pdfuaid: bool, creator_tool: str) -> str:
    """The cached static packet prefix (up to and including ``rdf:about``)."""
    key = (pdfuaid, creator_tool)
    prefix = _XMP_PREFIX_CACHE.get(key)
    if prefix is None:
        namespaces = [
            f'xmlns:rdf="{_NS_RDF}"',
            f'xmlns:xmp="{_NS_XMP}"',
            f'xmlns:dc="{_NS_DC}"',
            f'xmlns:pdf="{_NS_PDF}"',
            f'xmlns:pdfaid="{_NS_PDFAID}"',
            f'xmlns:xmpMM="{_NS_XMPMM}"',
        ]
        if pdfuaid:
            namespaces.append(f'xmlns:pdfuaid="{_NS_PDFUAID}"')
            namespaces.append(f'xmlns:pdfaExtension="{_NS_PDFA_EXTENSION}"')
        prefix = (
            '<?xpacket begin="\ufeff" id="{id}"?>\n'
            '<x:xmpmeta xmlns:x="{ns_x}" x:xmptk="{tool}">\n'
            ' <rdf:RDF {namespaces}>\n'
            '  <rdf:Description rdf:about="">\n'
        ).format(
            id=XMP_PACKET_ID,
            ns_x=_NS_X,
            tool=creator_tool,
            namespaces=" ".join(namespaces),
        )
        _XMP_PREFIX_CACHE[key] = prefix
    return prefix


def build_xmp_packet(
    *,
    created: datetime.datetime,
    producer: str,
    creator_tool: str = "pythoncoreengine",
    title: Optional[str] = None,
    creator: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    pdfaid_part: Optional[int] = 4,
    pdfaid_rev: Optional[int] = 2020,
    pdfuaid_part: Optional[int] = None,
    pdfuaid_rev: Optional[int] = None,
) -> bytes:
    """Return a complete XMP packet (>= 2048 bytes, deterministic).

    The packet header carries the UTF-8 byte-order mark inside the
    ``begin`` attribute (the bytes ``EF BB BF``) followed by the fixed
    XMP packet ID.  ``dc:title`` / ``dc:creator`` / ``dc:subject`` /
    ``dc:description`` are emitted only when provided.  When
    ``pdfuaid_part`` is given, the packet also declares the pdfuaid
    namespace and registers it via ``pdfaExtension`` (ISO 19005-4 6.7.3.2).

    Phase 6: the static prefix/suffix come from the module cache; only the
    property body and padding are assembled per call, and the result stays
    byte-identical to the phase-4/5 packet layout.
    """
    values = build_xmp_dict(
        created=created,
        producer=producer,
        creator_tool=creator_tool,
        title=title,
        creator=creator,
        subject=subject,
        description=description,
        pdfaid_part=pdfaid_part,
        pdfaid_rev=pdfaid_rev,
        pdfuaid_part=pdfuaid_part,
        pdfuaid_rev=pdfuaid_rev,
    )
    with_uaid = pdfuaid_part is not None
    body = "\n".join(f"   <{key}>{value}</{key}>" for key, value in values.items())
    if with_uaid:
        body = body + "\n" + _PDFA_EXTENSION_SCHEMAS.rstrip("\n")
    # Note: no XML declaration -- an XML declaration must be the first
    # markup in a document, and the xpacket header PI precedes it here,
    # so real producers (Adobe, Ghostscript) omit it; the UTF-8 encoding
    # is inferred from the BOM in the xpacket header.
    encoded = (_xmp_prefix(with_uaid, creator_tool) + body + _XMP_SUFFIX).encode("utf-8")
    end = b'<?xpacket end="w"?>\n'
    space = max(0, XMP_PACKET_MIN_SIZE - len(encoded) - len(end))
    total = len(encoded) + 1 + space + len(end)
    space += (4 - total % 4) % 4  # round the packet length up to a 4-multiple
    padding = b"\n" + b" " * space
    return encoded + padding + end

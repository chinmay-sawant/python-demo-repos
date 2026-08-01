"""PDF digital signatures: signature fields, byte-range placeholders, CMS splice.

Phase 7.4 implements the full signature pipeline in pure Python:

* A signature form field (widget with ``/FT /Sig``) whose ``/V`` is a
  signature dictionary: ``/Type /Sig``, ``/Filter /Adobe.PPKLite``,
  ``/SubFilter /adbe.pkcs7.sha1`` (broad reader support; not CAdES), a
  ``/ByteRange`` of four fixed-width splice slots, a ``/Contents`` hex
  string reserved at a fixed byte capacity, ``/M`` (a fixed-length date
  slot), plus optional ``/Reason``, ``/Location``, ``/ContactInfo`` and
  ``/Name``.
* :func:`sign_pdf` performs the classic in-place byte-range splice: the
  document is rendered once with the placeholder (zeros) in place; the
  final byte offsets of the ``/Contents`` slot are located; the
  ``/ByteRange`` offsets are rewritten in their fixed-width slots (file
  length unchanged); the SHA-256 of the two covered slices is signed with
  an RSA key (engine.crypto); the resulting CMS/PKCS#7 ``SignedData`` is
  hex-encoded into the placeholder; and the ``/M`` slot is filled with the
  signing time.  Because the ``/Contents`` slot lies between the two
  signed ranges, the digest is independent of the signature bytes and the
  final file is exactly the placeholder file with the signature spliced in
  -- every xref offset stays valid, so the signed file parses identically.

The signature field is a form field, so it integrates with the
:class:`~engine.form.FormManager`: the widget appears in ``/AcroForm
/Fields`` and the page's ``/Annots``, and under tagged output it gets a
``/Form`` structure element and ``/StructParent`` like any other widget
(PDF/UA-2 clause 8.10.1).  An empty appearance stream is emitted so
PDF/A-4's annotation-appearance rule (ISO 19005-4:2020 6.3.3) stays
satisfied; the field renders invisibly.

Compliance: signing is OFF by default.  Signed plain-PDF-2.0 documents are
the non-compliant fast path; signed compliant fixtures are expected to
still pass ``verapdf -f 4`` (the byte-range splice changes no offsets) and
are best-effort for ``-f ua2``.  This signing is NOT production security:
keys are seeded and uncertified (see engine.crypto), and the CMS embeds no
certificate chain, so readers cannot anchor trust.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from .crypto import (
    DEFAULT_SIGNER_NAME,
    RSAPrivateKey,
    build_cms_signed_data,
)
from .form import FormField, FormManager
from .write import N, ObjectId, PdfHexString, FixedDigits, format_date

__all__ = [
    "CONTENTS_CAPACITY",
    "SIGNATURE_FIELD_NAME",
    "SignatureManager",
    "parse_signature_dictionary",
    "sign_pdf",
]

#: The byte capacity reserved for the CMS ``/Contents`` value.  16 KiB is
#: ample for a 2048-bit RSA signature (CMS overhead is roughly 500 bytes);
#: the placeholder renders as ``2 * CONTENTS_CAPACITY`` zero hex digits.
CONTENTS_CAPACITY = 16 * 1024

#: The fixed-width ``/ByteRange`` placeholder as it appears in the file.
_BYTE_RANGE_PLACEHOLDER = b"[0000000000 0000000000 0000000000 0000000000]"

#: The fixed-length ``/M`` placeholder (escaped PDF date string literal:
#: ``(\\(D:00010101000000\\))`` -- parens are escaped by escape_string).
_M_PLACEHOLDER = b"(\\(D:00010101000000\\))"

#: The escaped opening/closing parenthesis pair (for the /M splice).
_M_OPEN = b"\\("
_M_CLOSE = b"\\)"

#: The default field name of the auto-created signature field.
SIGNATURE_FIELD_NAME = "Signature1"


class SignatureManager:
    """Per-document signature bookkeeping: the signature dictionary object.

    The manager reserves the ``/V`` signature dictionary (a value object
    carrying the fixed-width placeholders) and asks the document's
    :class:`~engine.form.FormManager` to create the ``/FT /Sig`` widget
    that references it.  Bodies attach at render time through the host's
    deferred queue, matching the phase-7.3 widget flow; the rendered bytes
    still hold zeros in the ``/ByteRange``/``/Contents``/``/M`` slots until
    :func:`sign_pdf` splices the real values in.
    """

    def __init__(
        self,
        *,
        reserve_value: Callable[[Callable[[], Any]], ObjectId],
        form_manager: FormManager,
    ) -> None:
        self._reserve_value = reserve_value
        self._form_manager = form_manager
        self._signature_ref: Optional[ObjectId] = None

    @property
    def signature_ref(self) -> ObjectId:
        """The reserved signature dictionary object reference."""
        if self._signature_ref is None:
            raise ValueError("no signature field added yet")
        return self._signature_ref

    def add_signature_field(
        self,
        name: str = SIGNATURE_FIELD_NAME,
        *,
        page_index: int,
        rect: Sequence[float],
        reason: Optional[str] = None,
        location: Optional[str] = None,
        contact_info: Optional[str] = None,
        signer_name: Optional[str] = None,
    ) -> FormField:
        """Create the ``/FT /Sig`` widget field and its signature dictionary.

        ``rect`` is in PDF user space (like the other widget helpers).
        The meta entries (``/Reason``, ``/Location``, ``/ContactInfo``,
        ``/Name``) land in the signature dictionary at render time; the
        per-signing values (``/M``, the CMS itself) are fixed by
        :func:`sign_pdf`.
        """
        if self._signature_ref is not None:
            raise ValueError("this document already has a signature field")
        sig_dict = {
            N("Type"): N("Sig"),
            N("Filter"): N("Adobe.PPKLite"),
            N("SubFilter"): N("adbe.pkcs7.sha1"),
            N("ByteRange"): _byte_range_placeholder(),
            N("Contents"): PdfHexString(bytes(CONTENTS_CAPACITY)),
            N("M"): "(D:00010101000000)",
        }
        if reason is not None:
            sig_dict[N("Reason")] = reason
        if location is not None:
            sig_dict[N("Location")] = location
        if contact_info is not None:
            sig_dict[N("ContactInfo")] = contact_info
        if signer_name is not None:
            sig_dict[N("Name")] = signer_name
        self._signature_ref = self._reserve_value(lambda: sig_dict)
        return self._form_manager.add_signature_field(
            name, self._signature_ref, page_index=page_index, rect=rect
        )


def _byte_range_placeholder() -> List[Any]:
    """The four fixed-width zero slots the signer overwrites in place."""
    return [FixedDigits(0), FixedDigits(0), FixedDigits(0), FixedDigits(0)]


# ---------------------------------------------------------------------------
# Byte-range splice
# ---------------------------------------------------------------------------

#: The full zero-hex /Contents placeholder as it appears in the file.
_CONTENTS_ZEROS = b"00" * CONTENTS_CAPACITY


def _find_slot_before(data: bytes, marker: bytes, start: int, limit: int) -> int:
    """The byte offset of ``marker`` within the ``limit`` bytes before ``start``."""
    window = data[max(0, start - limit) : start]
    pos = window.rfind(marker)
    if pos < 0:
        raise ValueError(
            f"signature placeholder {marker[:16]!r}... not found near offset {start}"
        )
    return max(0, start - limit) + pos


def sign_pdf(
    pdf_bytes: bytes,
    key: RSAPrivateKey,
    *,
    signing_time: Optional[datetime.datetime] = None,
    signer_name: str = DEFAULT_SIGNER_NAME,
    serial_number: int = 1,
) -> bytes:
    """Sign a placeholder PDF: byte-range digest, RSA-SHA256, CMS splice.

    ``pdf_bytes`` must be the output of a document rendered with a
    :class:`SignatureManager` placeholder (or any file carrying the same
    placeholder markers).  The splice keeps the file length identical, so
    every xref offset remains valid.  The first still-unsigned field is
    signed; call again for further fields.

    Args:
        pdf_bytes: rendered PDF bytes containing the placeholder.
        key: the RSA private key (engine.crypto) used to sign.
        signing_time: the ``/M`` date and CMS ``signingTime`` (defaults to
            now; pass a fixed value for deterministic output).
        signer_name: the synthetic issuer CN of the CMS signer identifier.
        serial_number: the synthetic issuer serial number.

    Returns:
        The signed PDF bytes, byte-for-byte the same length as ``pdf_bytes``.
    """
    if len(pdf_bytes) < CONTENTS_CAPACITY * 2 + 64:
        raise ValueError("PDF too small to carry a signature placeholder")
    when = signing_time if signing_time is not None else datetime.datetime.now()

    contents_start = pdf_bytes.find(_CONTENTS_ZEROS)
    if contents_start < 0:
        raise ValueError(
            "no signature placeholder found: render with a SignatureManager "
            "(builder signing=True) first"
        )
    contents_end = contents_start + len(_CONTENTS_ZEROS)
    byte_range_start = _find_slot_before(
        pdf_bytes, _BYTE_RANGE_PLACEHOLDER, contents_start, 4096
    )
    byte_range_end = byte_range_start + len(_BYTE_RANGE_PLACEHOLDER)
    # /M sits after /Contents in the signature dictionary.
    m_start = pdf_bytes.find(_M_PLACEHOLDER, contents_end, contents_end + 4096)
    if m_start < 0:
        raise ValueError("no /M placeholder found after /Contents")
    m_end = m_start + len(_M_PLACEHOLDER)

    byte_range = (
        0,
        contents_start,
        contents_end - contents_start,
        len(pdf_bytes) - contents_end,
    )
    new_slot = b"[%010d %010d %010d %010d]" % byte_range
    if len(new_slot) != len(_BYTE_RANGE_PLACEHOLDER):
        raise AssertionError("ByteRange splice changed file length")

    new_m = b"(" + _M_OPEN + format_date(when).encode("ascii") + _M_CLOSE + b")"
    if len(new_m) != len(_M_PLACEHOLDER):
        raise AssertionError("date splice changed file length")

    out = bytearray(pdf_bytes)
    out[byte_range_start:byte_range_end] = new_slot
    out[m_start:m_end] = new_m

    # The digest covers everything except the /Contents slot itself: the
    # /ByteRange and /M splices above happened first, so the hash is over
    # the exact bytes that will be validated against the CMS.
    digest = _hash_ranges(bytes(out), contents_start, contents_end)
    cms = build_cms_signed_data(
        digest,
        key,
        signing_time=when,
        signer_name=signer_name,
        serial_number=serial_number,
    )
    hex_sig = cms.hex().encode("ascii")
    if len(hex_sig) > len(_CONTENTS_ZEROS):
        raise ValueError(
            f"CMS ({len(hex_sig) // 2} bytes) exceeds the reserved "
            f"placeholder capacity ({CONTENTS_CAPACITY} bytes)"
        )
    out[contents_start:contents_end] = hex_sig.ljust(len(_CONTENTS_ZEROS), b"0")
    return bytes(out)


def _hash_ranges(data: bytes, contents_start: int, contents_end: int) -> bytes:
    """SHA-256 over the two /ByteRange slices around the /Contents slot.

    Range 1 is ``[0, contents_start)`` and range 2 is ``[contents_end,
    end)`` where ``contents_end`` points just past the last zero hex
    digit (at the closing ``>``), matching the /ByteRange the verifier
    recomputes from the dictionary.
    """
    import hashlib

    digest = hashlib.sha256()
    digest.update(data[:contents_start])
    digest.update(data[contents_end:])
    return digest.digest()


def parse_signature_dictionary(body: bytes) -> Optional[Dict[str, Any]]:
    """Parse the raw bytes of a signature dictionary object (test helper).

    ``body`` is the object body between ``N 0 obj`` and ``endobj`` (see
    ``engine.tests.helpers.object_bytes``).  Returns ``None`` when the
    body is not a signature dictionary; otherwise a dict with
    ``byte_range`` (the four ints), ``contents`` (raw CMS bytes,
    zero-padded to the placeholder capacity), ``m`` (the /M date string)
    and ``meta`` (Reason/Location/ContactInfo/Name as a raw dict bytes
    fragment for byte-level assertions).
    """
    if b"/Type /Sig" not in body:
        return None
    br_match = re.search(rb"/ByteRange\s*\[\s*([\d\s]+)\]", body)
    if br_match is None:
        return None
    byte_range = [int(value) for value in br_match.group(1).split()]
    if len(byte_range) != 4:
        raise ValueError(f"malformed /ByteRange in {body[:80]!r}")
    contents_hex = re.search(rb"/Contents\s*<([0-9a-f]+)>", body)
    if contents_hex is None:
        raise ValueError("signature dictionary missing /Contents")
    m_match = re.search(rb"/M\s*\((?:\\\()?(D:\d{14})(?:\\\))?\)", body)
    return {
        "byte_range": byte_range,
        "contents": bytes.fromhex(contents_hex.group(1).decode("ascii")),
        "m": m_match.group(1).decode("ascii") if m_match else None,
        "meta": body,
    }

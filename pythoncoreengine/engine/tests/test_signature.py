"""End-to-end tests for the PDF digital-signature pipeline (engine.signature).

Builds documents through ``DocumentBuilder(signing=True)`` and verifies,
from the emitted bytes: the signature widget (``/FT /Sig`` with ``/V``
pointing at the signature dictionary), the signature dictionary
(``/Type /Sig``, ``/Filter /Adobe.PPKLite``, ``/SubFilter
/adbe.pkcs7.sha1``, the fixed-width ``/ByteRange`` placeholder and the
zeroed ``/Contents`` hex string), and the tagged path (``/Form`` structure
element + ``/StructParent`` like any widget).

Then :func:`sign_pdf` splices the CMS in: the signed file is byte-for-byte
the same length as the placeholder file (so every xref offset stays
valid), the ``/ByteRange`` values point exactly at the ``/Contents`` hex
digits, the SHA-256 over the two covered slices equals the CMS
``messageDigest`` attribute, and -- the crucial check -- the RSA signature
inside the CMS verifies over the retagged signedAttrs encoding with the
embedded public key, entirely in pure Python.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import unittest

from engine import DocumentBuilder
from engine.crypto import (
    OID_MESSAGE_DIGEST,
    generate_rsa_key,
    rsa_verify_pkcs1v15,
)
from engine.signature import (
    CONTENTS_CAPACITY,
    parse_signature_dictionary,
    sign_pdf,
)
from engine.tests.helpers import (
    der_children,
    der_encode_tlv,
    der_oid_from_value,
    der_read_element,
    find_object_with,
    object_bytes,
    parse_xref,
    startxref_offset,
)

FIXED_TIME = datetime.datetime(2026, 8, 1, 12, 0, 0)
KEY = generate_rsa_key(2048, seed=7)
PUBLIC = KEY.public_key()


def _build_unsigned(*, signing: bool = True, **kwargs) -> bytes:
    builder = DocumentBuilder(created=FIXED_TIME, signing=signing, **kwargs)
    flow = builder.flow()
    flow.text("Signed document", size=16)
    flow.paragraph("Body text covered by the signature. " * 20, size=11)
    if signing:
        builder.add_signature_field(
            "Signature1",
            page_index=0,
            x=100,
            y=120,
            width=200,
            height=18,
            reason="Approval",
            location="Test lab",
            contact_info="qa@example.com",
        )
    return builder.render()


def _widget_and_sig_dict(data: bytes):
    """Return (widget body, signature dictionary body) from xref offsets."""
    offsets = parse_xref(data)
    widget_id = find_object_with(data, b"/FT /Sig", offsets)
    widget = object_bytes(data, offsets[widget_id])
    v_ref = int(re.search(rb"/V\s+(\d+)\s+0\s+R", widget).group(1))
    return widget, object_bytes(data, offsets[v_ref])


def _cms_signature_info(cms: bytes) -> dict:
    """Parse the CMS; returns signerInfo fields and the signedAttrs digest."""
    _tag, value, _next = der_read_element(cms)
    children = der_children(value)
    sd_tag, sd_content, _ = der_read_element(children[1][1])
    assert sd_tag == 0x30, hex(sd_tag)
    sd = der_children(sd_content)
    signer_fields = der_children(der_children(sd[3][1])[0][1])
    signed_attrs_tlv = der_encode_tlv(0xA0, signer_fields[3][1])
    return {
        "signed_attrs_tlv": signed_attrs_tlv,
        "signature": signer_fields[5][1],
        "attrs_digest": hashlib.sha256(signed_attrs_tlv).digest(),
    }


class TestSignatureFieldStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _build_unsigned()
        self.offsets = parse_xref(self.data)
        self.widget, self.sig_dict = _widget_and_sig_dict(self.data)

    def test_widget_keys(self) -> None:
        self.assertIn(b"/Subtype /Widget", self.widget)
        self.assertIn(b"/FT /Sig", self.widget)
        self.assertIn(b"/T (Signature1)", self.widget)
        self.assertRegex(self.widget, rb"/V\s+(\d+)\s+0\s+R")
        self.assertRegex(self.widget, rb"/AP << /N (\d+) 0 R >>")
        self.assertIn(b"/F 4", self.widget)
        self.assertIn(b"/Contents (Signature1)", self.widget)

    def test_signature_dictionary_keys(self) -> None:
        self.assertIn(b"/Type /Sig", self.sig_dict)
        self.assertIn(b"/Filter /Adobe.PPKLite", self.sig_dict)
        self.assertIn(b"/SubFilter /adbe.pkcs7.sha1", self.sig_dict)
        self.assertIn(b"/Reason (Approval)", self.sig_dict)
        self.assertIn(b"/Location (Test lab)", self.sig_dict)
        self.assertIn(b"/ContactInfo (qa@example.com)", self.sig_dict)

    def test_byte_range_placeholder_is_fixed_width(self) -> None:
        self.assertIn(
            b"/ByteRange [0000000000 0000000000 0000000000 0000000000]", self.sig_dict
        )

    def test_contents_placeholder_is_zeroed(self) -> None:
        expected = b"<" + b"00" * CONTENTS_CAPACITY + b">"
        self.assertIn(b"/Contents " + expected, self.sig_dict)

    def test_widget_in_acroform_fields(self) -> None:
        acroform = object_bytes(
            self.data,
            self.offsets[find_object_with(self.data, b"/Type /AcroForm", self.offsets)],
        )
        self.assertIn(b"/Fields [", acroform)
        widget_num = int(re.search(rb"(\d+) 0 obj", self.widget).group(1))
        self.assertRegex(acroform, rb"/Fields \[\s*%d 0 R" % widget_num)

    def test_empty_appearance_stream(self) -> None:
        ap_ref = int(re.search(rb"/N (\d+) 0 R", self.widget).group(1))
        ap = object_bytes(self.data, self.offsets[ap_ref])
        self.assertIn(b"/Subtype /Form", ap)
        self.assertIn(b"/BBox [0 0 200 18]", ap)
        self.assertIn(b"/Length 0", ap)

    def test_no_signature_without_flag(self) -> None:
        data = _build_unsigned(signing=False)
        self.assertNotIn(b"/FT /Sig", data)
        self.assertNotIn(b"/Type /Sig", data)
        self.assertNotIn(b"/AcroForm", data)

    def test_tagged_widget_has_structure(self) -> None:
        data = _build_unsigned(mode_pdfa4=True, mode_pdfua2=True, title="Signed")
        offsets = parse_xref(data)
        widget, _ = _widget_and_sig_dict(data)
        self.assertRegex(widget, rb"/StructParent (\d+)")
        key = int(re.search(rb"/StructParent (\d+)", widget).group(1))
        root = object_bytes(
            data, offsets[find_object_with(data, b"/Type /StructTreeRoot", offsets)]
        )
        parent_tree_id = int(
            re.search(rb"/ParentTree\s+(\d+)\s+0\s+R", root).group(1)
        )
        parent_tree = object_bytes(data, offsets[parent_tree_id])
        match = re.search(rb"%d\s+(\d+)\s+0\s+R" % key, parent_tree)
        self.assertIsNotNone(match, parent_tree)
        elem = object_bytes(data, offsets[int(match.group(1))])
        self.assertIn(b"/Type /StructElem", elem)
        self.assertIn(b"/S /Form", elem)
        self.assertIn(b"/OBJR", elem)

    def test_two_signature_fields_are_forbidden(self) -> None:
        builder = DocumentBuilder(created=FIXED_TIME, signing=True)
        flow = builder.flow()
        flow.text("x", size=12)
        builder.add_signature_field("A", page_index=0, x=10, y=10, width=100, height=18)
        with self.assertRaises(ValueError):
            builder.add_signature_field(
                "B", page_index=0, x=10, y=40, width=100, height=18
            )


class TestSignPdfSplice(unittest.TestCase):
    def setUp(self) -> None:
        self.unsigned = _build_unsigned()
        self.signed = sign_pdf(self.unsigned, KEY, signing_time=FIXED_TIME)
        self.offsets = parse_xref(self.signed)
        _widget, sig_dict = _widget_and_sig_dict(self.signed)
        self.parsed = parse_signature_dictionary(sig_dict)

    def test_length_unchanged_and_xref_intact(self) -> None:
        self.assertEqual(len(self.signed), len(self.unsigned))
        self.assertEqual(startxref_offset(self.signed), startxref_offset(self.unsigned))
        # Every xref offset still parses as an object header.
        for obj_id, offset in sorted(self.offsets.items()):
            match = re.match(rb"(\d+)\s+0\s+obj\b", self.signed[offset : offset + 16])
            self.assertIsNotNone(match, (obj_id, offset))
            self.assertEqual(int(match.group(1)), obj_id)

    def test_byte_range_spans_file_except_contents(self) -> None:
        br = self.parsed["byte_range"]
        self.assertEqual(br[0], 0)
        self.assertEqual(br[0] + br[1] + br[2] + br[3], len(self.signed))
        # The signed ranges exclude exactly the /Contents hex digits: the
        # byte at br[1] - 1 is the opening '<' and the byte at br[1] +
        # br[2] is the closing '>'.
        self.assertEqual(self.signed[br[1] - 1 : br[1]], b"<")
        self.assertEqual(self.signed[br[1] + br[2] : br[1] + br[2] + 1], b">")
        self.assertEqual(br[2], CONTENTS_CAPACITY * 2)
        # The placeholder file has the same layout (identical length).
        unsigned_offsets = parse_xref(self.unsigned)
        _w, sig_dict = _widget_and_sig_dict(self.unsigned)
        self.assertIn(b"/ByteRange [0000000000 0000000000 0000000000 0000000000]", sig_dict)

    def test_contents_holds_cms_with_zero_padding(self) -> None:
        contents = self.parsed["contents"]
        self.assertEqual(len(contents), CONTENTS_CAPACITY)
        cms = contents.rstrip(b"\x00")
        self.assertGreater(len(cms), 0)
        self.assertNotEqual(cms, contents)  # placeholder was zeroed
        # The CMS is a SEQUENCE whose content starts with the signedData OID.
        tag, value, _next = der_read_element(cms)
        self.assertEqual(tag, 0x30)
        self.assertEqual(der_oid_from_value(der_children(value)[0][1]), "1.2.840.113549.1.7.2")

    def test_m_date_spliced(self) -> None:
        self.assertEqual(self.parsed["m"], "D:20260801120000")

    def test_recomputed_range_hash_matches_message_digest(self) -> None:
        br = self.parsed["byte_range"]
        recomputed = hashlib.sha256()
        recomputed.update(self.signed[br[0] : br[0] + br[1]])
        recomputed.update(self.signed[br[1] + br[2] : br[1] + br[2] + br[3]])
        cms = self.parsed["contents"].rstrip(b"\x00")
        message_digest = _extract_message_digest(cms)
        self.assertEqual(message_digest, recomputed.digest())

    def test_cms_signature_verifies_with_public_key(self) -> None:
        cms = self.parsed["contents"].rstrip(b"\x00")
        info = _cms_signature_info(cms)
        self.assertTrue(
            rsa_verify_pkcs1v15(PUBLIC, info["attrs_digest"], info["signature"])
        )

    def test_tampered_byte_fails_verification(self) -> None:
        tampered = bytearray(self.signed)
        # Flip one byte in the first signed range (past the header).
        tampered[500] ^= 0x01
        tampered = bytes(tampered)
        br = self.parsed["byte_range"]
        recomputed = hashlib.sha256()
        recomputed.update(tampered[br[0] : br[0] + br[1]])
        recomputed.update(tampered[br[1] + br[2] : br[1] + br[2] + br[3]])
        cms = self.parsed["contents"].rstrip(b"\x00")
        self.assertNotEqual(_extract_message_digest(cms), recomputed.digest())

    def test_deterministic_signing(self) -> None:
        second = sign_pdf(self.unsigned, KEY, signing_time=FIXED_TIME)
        self.assertEqual(self.signed, second)

    def test_sign_with_different_key_fails_verification(self) -> None:
        other = generate_rsa_key(2048, seed=99)
        signed_other = sign_pdf(self.unsigned, other, signing_time=FIXED_TIME)
        cms = parse_signature_dictionary(_sig_dict_body(signed_other))[
            "contents"
        ].rstrip(b"\x00")
        info = _cms_signature_info(cms)
        self.assertFalse(
            rsa_verify_pkcs1v15(PUBLIC, info["attrs_digest"], info["signature"])
        )


def _extract_message_digest(cms: bytes) -> bytes:
    """The value of the messageDigest signed attribute inside ``cms``."""
    _tag, value, _next = der_read_element(cms)
    children = der_children(value)
    sd_tag, sd_content, _ = der_read_element(children[1][1])
    assert sd_tag == 0x30, hex(sd_tag)
    sd = der_children(sd_content)
    signer_fields = der_children(der_children(sd[3][1])[0][1])
    for attr in der_children(signer_fields[3][1]):
        type_oid = der_oid_from_value(der_children(attr[1])[0][1])
        if type_oid == OID_MESSAGE_DIGEST:
            attr_value = der_children(attr[1])[1]
            return der_children(attr_value[1])[0][1]
    raise AssertionError("no messageDigest attribute in CMS")


def _sig_dict_body(data: bytes) -> bytes:
    """The signature dictionary body of ``data`` (for /Contents access)."""
    _widget, sig_dict = _widget_and_sig_dict(data)
    return sig_dict


class TestSignPdfErrors(unittest.TestCase):
    def test_no_placeholder_raises(self) -> None:
        builder = DocumentBuilder(created=FIXED_TIME)
        flow = builder.flow()
        flow.text("plain", size=12)
        plain = builder.render()
        with self.assertRaises(ValueError):
            sign_pdf(plain, KEY)

    def test_tiny_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            sign_pdf(b"too small", KEY)

    def test_raw_placeholder_bytes_without_builder(self) -> None:
        # sign_pdf works on ANY bytes carrying the placeholder markers, so
        # a hand-assembled (non-builder) signature dictionary is signable.
        from engine.signature import _BYTE_RANGE_PLACEHOLDER, _M_PLACEHOLDER, _CONTENTS_ZEROS

        head = b"%PDF-2.0\n"
        sig_dict = (
            b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.sha1 "
            b"/ByteRange " + _BYTE_RANGE_PLACEHOLDER +
            b" /Contents <" + _CONTENTS_ZEROS + b"> "
            b"/M " + _M_PLACEHOLDER + b" >>"
        )
        raw = head + sig_dict + b"\ntrailer\n<< /Size 1 >>\n%%EOF"
        signed = sign_pdf(raw, KEY, signing_time=FIXED_TIME)
        self.assertEqual(len(signed), len(raw))
        # The CMS hex fills the head of the slot; the tail stays zero-
        # padded up to the closing '>', and the placeholders are gone.
        self.assertIn(b"00" * 8 + b">", signed)
        self.assertNotIn(_BYTE_RANGE_PLACEHOLDER, signed)
        self.assertNotIn(_CONTENTS_ZEROS, signed)


class TestComplianceFixtures(unittest.TestCase):
    def test_signed_nocomply_parses(self) -> None:
        data = _build_unsigned()
        signed = sign_pdf(data, KEY, signing_time=FIXED_TIME)
        offsets = parse_xref(signed)
        self.assertGreater(len(offsets), 5)
        self.assertEqual(len(signed), len(data))

    def test_signed_compliant_structure(self) -> None:
        data = _build_unsigned(mode_pdfa4=True, mode_pdfua2=True, title="Signed")
        signed = sign_pdf(data, KEY, signing_time=FIXED_TIME)
        self.assertEqual(len(signed), len(data))
        parsed = parse_signature_dictionary(_sig_dict_body(signed))
        br = parsed["byte_range"]
        self.assertEqual(br[0] + br[1] + br[2] + br[3], len(signed))
        cms = parsed["contents"].rstrip(b"\x00")
        info = _cms_signature_info(cms)
        self.assertTrue(
            rsa_verify_pkcs1v15(PUBLIC, info["attrs_digest"], info["signature"])
        )


if __name__ == "__main__":
    unittest.main()

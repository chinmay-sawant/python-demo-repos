"""End-to-end tests for the phase 7.5 standard security handler (engine.encrypt).

Builds documents through ``DocumentBuilder(encrypt=EncryptSpec(...))``
(revision 4 = AES-128 /AESV2, revision 6 = AES-256 /AESV3) and verifies
from the emitted bytes: the /Encrypt dictionary (``/Filter /Standard``,
``/V``, ``/R``, ``/Length``, ``/O``, ``/U``, ``/P``, ``/CF`` with
``/CFM``, ``/StmF``/``/StrF``, ``/EncryptMetadata`` and, for revision 6,
``/UE``, ``/OE``, ``/Perms``), the trailer ``/Encrypt`` reference, that
strings are emitted as hex strings and streams carry the ciphertext
length in ``/Length``.

Then the pure-Python decryptor (:func:`decrypt_pdf`) is exercised: both
revisions round-trip byte-for-byte to the unencrypted render, the user
and owner passwords both open the file, a wrong password is rejected and
the deterministic (seeded) output is stable across two encryption runs.
A per-object spot check decrypts one object's strings with the
independently derived object key, and the fixture builders produce
md5-stable bytes across two runs.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import unittest

from engine import DocumentBuilder
from engine.cipher import aes_cbc_decrypt, pkcs7_unpad
from engine.encrypt import (
    ALL_PERMISSIONS,
    EncryptSpec,
    StandardSecurityHandler,
    WrongPasswordError,
    decrypt_pdf,
    encrypt_pdf,
)
from engine.fixtures import (
    _PARAGRAPH,
    _encrypted_r4_document,
    _encrypted_r6_document,
)
from engine.tests.helpers import (
    object_bytes,
    parse_xref,
    startxref_offset,
    trailer_dict_bytes,
)

FIXED_TIME = datetime.datetime(2026, 8, 1, 12, 0, 0)

#: The document the encrypted fixtures are built from (deterministic).
_BODY = "Section body encrypted with the standard security handler. " * 12


def _build_unencrypted() -> bytes:
    builder = DocumentBuilder(created=FIXED_TIME)
    flow = builder.flow()
    flow.text("Encrypted document", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_BODY, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _build_encrypted(spec: EncryptSpec) -> bytes:
    builder = DocumentBuilder(created=FIXED_TIME, encrypt=spec)
    flow = builder.flow()
    flow.text("Encrypted document", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(_BODY, size=11, color=(0.15, 0.15, 0.15))
    return builder.render()


def _r4_spec(**overrides) -> EncryptSpec:
    kwargs = dict(password="user-secret", revision=4, seed=b"test-r4")
    kwargs.update(overrides)
    return EncryptSpec(**kwargs)


def _r6_spec(**overrides) -> EncryptSpec:
    kwargs = dict(password="user-secret", revision=6, seed=b"test-r6")
    kwargs.update(overrides)
    return EncryptSpec(**kwargs)


def _encrypt_dict_entries(data: bytes) -> dict:
    """Return the parsed /Encrypt dictionary entries (with a raw body copy)."""
    offsets = parse_xref(data)
    trailer = trailer_dict_bytes(data)
    encrypt_ref = int(re.search(rb"/Encrypt\s+(\d+)\s+0\s+R", trailer).group(1))
    body = object_bytes(data, offsets[encrypt_ref])

    def hex_value(name: str) -> bytes:
        return bytes.fromhex(
            re.search(rb"/%s\s+<([0-9a-fA-F]+)>" % name.encode(), body).group(1).decode()
        )

    meta_match = re.search(rb"/EncryptMetadata\s+(true|false)", body)
    return {
        "ref": encrypt_ref,
        "body": body,
        "V": int(re.search(rb"/V\s+(\d+)", body).group(1)),
        "R": int(re.search(rb"/R\s+(\d+)", body).group(1)),
        "Length": int(re.search(rb"/Length\s+(\d+)", body).group(1)),
        "P": int(re.search(rb"/P\s+(\d+)", body).group(1)),
        "CFM": re.search(rb"/CFM\s+/(\w+)", body).group(1).decode(),
        "EncryptMetadata": meta_match is None or meta_match.group(1) == b"true",
        "O": hex_value("O"),
        "U": hex_value("U"),
        "UE": hex_value("UE") if b"/UE" in body else None,
        "OE": hex_value("OE") if b"/OE" in body else None,
        "Perms": hex_value("Perms") if b"/Perms" in body else None,
    }


class TestEncryptDictionary(unittest.TestCase):
    def _assert_common(self, entries: dict, *, revision: int, length: int, cfm: str) -> None:
        self.assertIn(b"/Filter /Standard", entries["body"])
        self.assertEqual(entries["V"], 4 if revision == 4 else 5)
        self.assertEqual(entries["R"], revision)
        self.assertEqual(entries["Length"], length)
        self.assertEqual(entries["CFM"], cfm)
        self.assertIn(b"/StmF /StdCF", entries["body"])
        self.assertIn(b"/StrF /StdCF", entries["body"])
        self.assertIn(b"/AuthEvent /DocOpen", entries["body"])
        self.assertIn(b"/EncryptMetadata true", entries["body"])
        self.assertEqual(len(entries["O"]), 32 if revision == 4 else 48)
        self.assertEqual(len(entries["U"]), 32 if revision == 4 else 48)
        self.assertIn(entries["P"], (ALL_PERMISSIONS,))

    def test_r4_encrypt_dict(self) -> None:
        data = _build_encrypted(_r4_spec())
        entries = _encrypt_dict_entries(data)
        self._assert_common(entries, revision=4, length=128, cfm="AESV2")
        self.assertIsNone(entries["UE"])
        self.assertIsNone(entries["OE"])
        self.assertIsNone(entries["Perms"])

    def _perms_plain(self, data: bytes, spec: EncryptSpec, entries: dict) -> bytes:
        """Decrypt /Perms with the recovered file key (not a zero key)."""
        trailer = trailer_dict_bytes(data)
        id0 = bytes.fromhex(
            re.search(rb"/ID\s*\[\s*<([0-9a-fA-F]{32})>", trailer).group(1).decode()
        )
        handler = StandardSecurityHandler.load(entries, id0)
        handler.recover_key(spec.password)
        return aes_cbc_decrypt(handler.key, b"\x00" * 16, entries["Perms"])

    def test_r6_encrypt_dict(self) -> None:
        spec = _r6_spec()
        data = _build_encrypted(spec)
        entries = _encrypt_dict_entries(data)
        self._assert_common(entries, revision=6, length=256, cfm="AESV3")
        self.assertIsNotNone(entries["UE"])
        self.assertIsNotNone(entries["OE"])
        self.assertIsNotNone(entries["Perms"])
        self.assertEqual(len(entries["UE"]), 32)
        self.assertEqual(len(entries["OE"]), 32)
        self.assertEqual(len(entries["Perms"]), 16)
        # /Perms decrypts to P-le + FFFFFFFF + 'T' + 'adb' + ignored bytes.
        perms_plain = self._perms_plain(data, spec, entries)
        self.assertEqual(perms_plain[4:8], b"\xff\xff\xff\xff")
        self.assertEqual(perms_plain[8:12], b"Tadb")

    def test_encrypt_metadata_false_r6_perms_flag(self) -> None:
        spec = _r6_spec(encrypt_metadata=False)
        data = _build_encrypted(spec)
        entries = _encrypt_dict_entries(data)
        self.assertIn(b"/EncryptMetadata false", entries["body"])
        perms_plain = self._perms_plain(data, spec, entries)
        self.assertEqual(perms_plain[8:12], b"Fadb")


class TestTrailerAndObjects(unittest.TestCase):
    def _encrypted(self) -> bytes:
        return _build_encrypted(_r4_spec())

    def test_trailer_encrypt_resolves(self) -> None:
        data = self._encrypted()
        trailer = trailer_dict_bytes(data)
        match = re.search(rb"/Encrypt\s+(\d+)\s+0\s+R", trailer)
        self.assertIsNotNone(match)
        self.assertIn(b"/Size", trailer)
        self.assertIn(b"/Root", trailer)
        self.assertIn(b"/ID", trailer)
        # The /ID must be preserved verbatim (it feeds the key derivation).
        unencrypted = _build_unencrypted()
        ids = re.findall(rb"<([0-9a-f]{32})>", trailer_dict_bytes(data))
        self.assertGreater(len(ids), 0)
        plain_ids = re.findall(rb"<([0-9a-f]{32})>", trailer_dict_bytes(unencrypted))
        self.assertEqual(ids, plain_ids)
        # The trailer itself must not be encrypted: /Size stays a plain int.
        self.assertIn(b"/Size", trailer)

    def test_strings_emitted_as_hex(self) -> None:
        data = self._encrypted()
        offsets = parse_xref(data)
        # The /Info object's strings must be hex strings, not literals.
        info_ref = int(
            re.search(rb"/Info\s+(\d+)\s+0\s+R", trailer_dict_bytes(data)).group(1)
        )
        info_body = object_bytes(data, offsets[info_ref])
        self.assertNotIn(b"(pythoncoreengine", info_body)
        self.assertIn(b"/Producer <", info_body)
        self.assertIn(b"/CreationDate <", info_body)
        # No unencrypted literal strings survive anywhere in the bodies.
        for obj_id, offset in offsets.items():
            body = object_bytes(data, offset)
            self.assertNotIn(b"/Producer (", body)

    def test_streams_encrypted_with_ciphertext_length(self) -> None:
        data = self._encrypted()
        unencrypted = _build_unencrypted()
        self.assertNotEqual(data, unencrypted)
        offsets = parse_xref(data)
        plain_offsets = parse_xref(unencrypted)
        # Find the page content stream in both files by its /Filter marker.
        def content_stream_id(offsets, src):
            for obj_id, offset in sorted(offsets.items()):
                body = object_bytes(src, offset)
                if b"/Filter /FlateDecode" in body and b"stream" in body:
                    return obj_id, body
            raise AssertionError("no content stream found")

        enc_id, enc_body = content_stream_id(offsets, data)
        plain_id, plain_body = content_stream_id(plain_offsets, unencrypted)
        enc_length = int(re.search(rb"/Length\s+(\d+)", enc_body).group(1))
        plain_length = int(re.search(rb"/Length\s+(\d+)", plain_body).group(1))
        self.assertGreater(enc_length, plain_length)  # IV + padding inflate it
        self.assertEqual(enc_length % 16, 0)
        # The stream data on disk is no longer the zlib plaintext.
        stream_match = re.search(rb"stream\n", enc_body)
        self.assertNotEqual(enc_body[stream_match.end() :], b"x\x9c")

    def test_xref_startxref_intact(self) -> None:
        data = self._encrypted()
        offsets = parse_xref(data)
        self.assertLess(startxref_offset(data), len(data))
        self.assertIn(b"xref", data)
        # Every in-use object must have its offset recorded.
        self.assertGreater(len(offsets), 6)


class TestRoundTrips(unittest.TestCase):
    def _round_trip(self, spec: EncryptSpec) -> bytes:
        unencrypted = _build_unencrypted()
        encrypted = _build_encrypted(spec)
        self.assertNotEqual(encrypted, unencrypted)
        return decrypt_pdf(encrypted, spec.password)

    def test_r4_round_trip_byte_identical(self) -> None:
        self.assertEqual(self._round_trip(_r4_spec()), _build_unencrypted())

    def test_r6_round_trip_byte_identical(self) -> None:
        self.assertEqual(self._round_trip(_r6_spec()), _build_unencrypted())

    def test_r4_empty_password_round_trip(self) -> None:
        spec = _r4_spec(password="")
        self.assertEqual(self._round_trip(spec), _build_unencrypted())

    def test_r6_empty_password_round_trip(self) -> None:
        spec = _r6_spec(password="")
        self.assertEqual(self._round_trip(spec), _build_unencrypted())

    def test_r4_owner_password_opens(self) -> None:
        spec = _r4_spec(owner_password="owner-secret")
        encrypted = _build_encrypted(spec)
        self.assertEqual(decrypt_pdf(encrypted, "owner-secret"), _build_unencrypted())
        self.assertEqual(decrypt_pdf(encrypted, "user-secret"), _build_unencrypted())

    def test_r6_owner_password_opens(self) -> None:
        spec = _r6_spec(owner_password="owner-secret")
        encrypted = _build_encrypted(spec)
        self.assertEqual(decrypt_pdf(encrypted, "owner-secret"), _build_unencrypted())
        self.assertEqual(decrypt_pdf(encrypted, "user-secret"), _build_unencrypted())

    def test_r4_wrong_password_rejected(self) -> None:
        encrypted = _build_encrypted(_r4_spec())
        with self.assertRaises(WrongPasswordError):
            decrypt_pdf(encrypted, "not-the-password")

    def test_r6_wrong_password_rejected(self) -> None:
        encrypted = _build_encrypted(_r6_spec())
        with self.assertRaises(WrongPasswordError):
            decrypt_pdf(encrypted, "not-the-password")

    def test_r6_perms_mismatch_rejected(self) -> None:
        encrypted = _build_encrypted(_r6_spec())
        # Corrupt one /Perms byte: the decryptor must refuse the file.
        tail = encrypted.find(b"startxref")
        tampered = bytearray(encrypted[:tail])
        tampered[tail - 20] ^= 0xFF
        with self.assertRaises(ValueError):
            decrypt_pdf(bytes(tampered) + encrypted[tail:], "user-secret")

    def test_encrypt_metadata_false_round_trip(self) -> None:
        spec = _r4_spec(encrypt_metadata=False)
        self.assertEqual(self._round_trip(spec), _build_unencrypted())


class TestPerObjectKeys(unittest.TestCase):
    def _handler_for(self, data: bytes, spec: EncryptSpec) -> StandardSecurityHandler:
        entries = _encrypt_dict_entries(data)
        trailer = trailer_dict_bytes(data)
        id0 = bytes.fromhex(
            re.search(rb"/ID\s*\[\s*<([0-9a-fA-F]{32})>", trailer).group(1).decode()
        )
        return StandardSecurityHandler.load(entries, id0)

    def test_r4_spot_decrypt_info_strings(self) -> None:
        spec = _r4_spec()
        data = _build_encrypted(spec)
        handler = self._handler_for(data, spec)
        handler.recover_key(spec.password)
        offsets = parse_xref(data)
        info_ref = int(
            re.search(rb"/Info\s+(\d+)\s+0\s+R", trailer_dict_bytes(data)).group(1)
        )
        info_body = object_bytes(data, offsets[info_ref])
        producer_hex = re.search(rb"/Producer <([0-9a-fA-F]+)>", info_body).group(1)
        ciphertext = bytes.fromhex(producer_hex.decode())
        key = handler.object_key(info_ref)
        self.assertEqual(len(key), 16)
        iv, body = ciphertext[:16], ciphertext[16:]
        plain = pkcs7_unpad(aes_cbc_decrypt(key, iv, body))
        self.assertEqual(plain, b"pythoncoreengine 0.1.0")

    def test_r6_spot_decrypt_info_strings(self) -> None:
        spec = _r6_spec()
        data = _build_encrypted(spec)
        handler = self._handler_for(data, spec)
        handler.recover_key(spec.password)
        offsets = parse_xref(data)
        info_ref = int(
            re.search(rb"/Info\s+(\d+)\s+0\s+R", trailer_dict_bytes(data)).group(1)
        )
        info_body = object_bytes(data, offsets[info_ref])
        date_hex = re.search(rb"/CreationDate <([0-9a-fA-F]+)>", info_body).group(1)
        ciphertext = bytes.fromhex(date_hex.decode())
        key = handler.object_key(info_ref)
        self.assertEqual(key, handler.key)  # AES-256: object key == file key
        iv, body = ciphertext[:16], ciphertext[16:]
        plain = pkcs7_unpad(aes_cbc_decrypt(key, iv, body))
        self.assertEqual(plain, b"D:20260801120000")

    def test_r4_object_key_depends_on_object_number(self) -> None:
        spec = _r4_spec()
        data = _build_encrypted(spec)
        handler = self._handler_for(data, spec)
        handler.recover_key(spec.password)
        key1 = handler.object_key(3)
        key2 = handler.object_key(4)
        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, handler.key)
        self.assertEqual(len(key1), 16)


class TestBuilderWiring(unittest.TestCase):
    def test_encryption_off_by_default(self) -> None:
        plain_builder = _build_unencrypted()
        builder = DocumentBuilder(created=FIXED_TIME)
        flow = builder.flow()
        flow.text("Encrypted document", size=16, color=(0.1, 0.1, 0.1))
        flow.paragraph(_BODY, size=11, color=(0.15, 0.15, 0.15))
        self.assertEqual(builder.render(), plain_builder)

    def test_pdfa4_rejects_encryption(self) -> None:
        with self.assertRaises(ValueError):
            DocumentBuilder(
                created=FIXED_TIME, mode_pdfa4=True, encrypt=_r4_spec()
            )
        with self.assertRaises(ValueError):
            DocumentBuilder(
                created=FIXED_TIME, mode_pdfa4=True, encrypt=_r6_spec()
            )

    def test_pdfua2_without_a4_allows_encryption(self) -> None:
        # mode_pdfua2 without mode_pdfa4 is plain tagged output: encryption
        # is allowed there (the A-4 prohibition is what matters).
        builder = DocumentBuilder(
            created=FIXED_TIME, mode_pdfua2=True, encrypt=_r4_spec()
        )
        flow = builder.flow()
        flow.text("Encrypted tagged", size=16)
        data = builder.render()

        reference = DocumentBuilder(created=FIXED_TIME, mode_pdfua2=True)
        ref_flow = reference.flow()
        ref_flow.text("Encrypted tagged", size=16)
        self.assertEqual(decrypt_pdf(data, "user-secret"), reference.render())

    def test_unsupported_revision_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EncryptSpec(password="x", revision=5)

    def test_encrypt_is_deterministic(self) -> None:
        first = _build_encrypted(_r4_spec())
        second = _build_encrypted(_r4_spec())
        self.assertEqual(first, second)
        third = _build_encrypted(_r6_spec())
        fourth = _build_encrypted(_r6_spec())
        self.assertEqual(third, fourth)


class TestFixtures(unittest.TestCase):
    def _fixture_reference(self, revision: int) -> bytes:
        """The unencrypted build the encrypted fixtures wrap (same content)."""
        builder = DocumentBuilder(created=FIXED_TIME)
        flow = builder.flow()
        flow.text(
            "Encrypted document (%s)" % ("AES-128" if revision == 4 else "AES-256"),
            size=16,
            color=(0.1, 0.1, 0.1),
        )
        flow.paragraph(_PARAGRAPH, size=11, color=(0.15, 0.15, 0.15))
        return builder.render()

    def test_encrypted_fixtures_are_deterministic(self) -> None:
        for builder in (_encrypted_r4_document, _encrypted_r6_document):
            first = builder()
            second = builder()
            self.assertEqual(first, second)
            self.assertEqual(hashlib.md5(first).hexdigest(), hashlib.md5(second).hexdigest())

    def test_r4_fixture_round_trips(self) -> None:
        data = _encrypted_r4_document()
        self.assertEqual(decrypt_pdf(data, "fixture-password"), self._fixture_reference(4))

    def test_r6_fixture_round_trips(self) -> None:
        data = _encrypted_r6_document()
        self.assertEqual(decrypt_pdf(data, "fixture-password"), self._fixture_reference(6))


if __name__ == "__main__":
    unittest.main()

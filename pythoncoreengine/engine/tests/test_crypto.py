"""Unit tests for the pure-Python crypto primitives (engine.crypto).

Covers: deterministic seeded RSA key generation (same seed -> identical
key), RSA-SHA256 PKCS#1 v1.5 sign/verify round-trips including padding
decode and tamper rejection, the DER encoder (round-tripped through the
tiny DER reader in engine.tests.helpers, plus known-answer byte checks for
the OIDs), and the CMS/PKCS#7 SignedData builder: the ContentInfo
structure, the signed attributes (contentType / signingTime /
messageDigest), the signer identifier and -- the crucial bit -- that the
RSA signature inside the CMS verifies over the A0-retagged signedAttrs
encoding with the embedded public key, all in pure Python.
"""

from __future__ import annotations

import datetime
import hashlib
import unittest

from engine.crypto import (
    OID_CONTENT_TYPE,
    OID_DATA,
    OID_MESSAGE_DIGEST,
    OID_RSA_ENCRYPTION,
    OID_SHA256,
    OID_SIGNED_DATA,
    OID_SIGNING_TIME,
    RSAPrivateKey,
    build_cms_signed_data,
    der_context_explicit,
    der_integer,
    der_null,
    der_octet_string,
    der_oid,
    der_sequence,
    der_set,
    generate_rsa_key,
    pkcs1_v1_5_decode,
    pkcs1_v1_5_encode,
    rsa_sign_pkcs1v15,
    rsa_verify_pkcs1v15,
    signed_attributes_input,
)
from engine.tests.helpers import (
    der_children,
    der_encode_tlv,
    der_int_from_value,
    der_oid_from_value,
    der_read_element,
)

FIXED_TIME = datetime.datetime(2026, 8, 1, 12, 0, 0)
DIGEST = hashlib.sha256(b"hello pdf").digest()

#: AlgorithmIdentifier DER for SHA-256 with NULL params (known answer).
SHA256_ALGORITHM = b"\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00"
#: rsaEncryption AlgorithmIdentifier with NULL params (known answer).
RSA_ALGORITHM = b"\x30\x0d\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00"


def _make_key(bits: int = 1024, seed: int = 42) -> RSAPrivateKey:
    return generate_rsa_key(bits, seed=seed)


class TestRSAGeneration(unittest.TestCase):
    def test_deterministic_per_seed(self) -> None:
        self.assertEqual(_make_key(1024, seed=7), _make_key(1024, seed=7))

    def test_different_seeds_differ(self) -> None:
        self.assertNotEqual(_make_key(1024, seed=7), _make_key(1024, seed=8))

    def test_modulus_size(self) -> None:
        key = _make_key(1024)
        self.assertEqual(key.n.bit_length(), 1024)
        self.assertEqual(key.size_bytes, 128)
        key2048 = _make_key(2048)
        self.assertEqual(key2048.n.bit_length(), 2048)
        self.assertEqual(key2048.size_bytes, 256)

    def test_public_exponent_and_primes(self) -> None:
        key = _make_key()
        self.assertEqual(key.e, 65537)
        self.assertEqual(key.n, key.p * key.q)
        # d * e == 1 mod lcm(p-1, q-1) (the Carmichael exponent).
        from engine.crypto import _mod_gcd

        lam = (key.p - 1) * (key.q - 1) // _mod_gcd(key.p - 1, key.q - 1)
        self.assertEqual((key.d * key.e) % lam, 1)

    def test_cached_generation_is_identical(self) -> None:
        first = generate_rsa_key(1024, seed=99)
        second = generate_rsa_key(1024, seed=99)
        self.assertEqual(first, second)

    def test_key_size_validation(self) -> None:
        with self.assertRaises(ValueError):
            generate_rsa_key(bits=128, seed=1)


class TestRSASignVerify(unittest.TestCase):
    def setUp(self) -> None:
        self.key = _make_key()
        self.public = self.key.public_key()

    def test_signature_length_is_modulus(self) -> None:
        signature = rsa_sign_pkcs1v15(self.key, DIGEST)
        self.assertEqual(len(signature), self.key.size_bytes)

    def test_round_trip(self) -> None:
        signature = rsa_sign_pkcs1v15(self.key, DIGEST)
        self.assertTrue(rsa_verify_pkcs1v15(self.public, DIGEST, signature))

    def test_padding_decode_round_trip(self) -> None:
        signature = rsa_sign_pkcs1v15(self.key, DIGEST)
        em = pow(int.from_bytes(signature, "big"), self.key.e, self.key.n).to_bytes(
            self.key.size_bytes, "big"
        )
        from engine.crypto import sha256_digest_info

        self.assertEqual(pkcs1_v1_5_decode(em), sha256_digest_info(DIGEST))

    def test_wrong_digest_rejected(self) -> None:
        signature = rsa_sign_pkcs1v15(self.key, DIGEST)
        other = hashlib.sha256(b"tampered").digest()
        self.assertFalse(rsa_verify_pkcs1v15(self.public, other, signature))

    def test_garbage_signature_rejected(self) -> None:
        garbage = bytes([0xFF] * self.key.size_bytes)
        self.assertFalse(rsa_verify_pkcs1v15(self.public, DIGEST, garbage))

    def test_wrong_length_rejected(self) -> None:
        self.assertFalse(rsa_verify_pkcs1v15(self.public, DIGEST, b"short"))

    def test_pkcs1_encode_known_prefix(self) -> None:
        em = pkcs1_v1_5_encode(DIGEST, 128)
        self.assertEqual(em[0:2], b"\x00\x01")
        self.assertEqual(em[2], 0xFF)
        # 74 filler bytes, then the 0x00 separator and the DigestInfo.
        self.assertEqual(em[75], 0xFF)
        self.assertEqual(em[76], 0x00)
        self.assertTrue(em[77:].startswith(b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48"))

    def test_pkcs1_encode_rejects_small_modulus(self) -> None:
        with self.assertRaises(ValueError):
            pkcs1_v1_5_encode(DIGEST, 20)

    def test_bad_padding_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pkcs1_v1_5_decode(b"\x00\x00" + b"\xff" * 100)


class TestDEREncoder(unittest.TestCase):
    """Round-trip every DER primitive through the tiny reader, plus
    known-answer byte checks for the values CMS depends on."""

    def test_integer_round_trip(self) -> None:
        for value in (0, 1, 127, 128, 255, 256, 65537, 2**1024):
            tag, payload, _next = der_read_element(der_integer(value))
            self.assertEqual(tag, 0x02)
            self.assertEqual(der_int_from_value(payload), value)

    def test_integer_high_bit_gets_zero_prefix(self) -> None:
        encoded = der_integer(128)
        self.assertEqual(encoded, b"\x02\x02\x00\x80")

    def test_negative_integer_rejected(self) -> None:
        with self.assertRaises(ValueError):
            der_integer(-1)

    def test_octet_string_round_trip(self) -> None:
        for raw in (b"", b"abc", bytes(range(256))):
            tag, payload, _next = der_read_element(der_octet_string(raw))
            self.assertEqual(tag, 0x04)
            self.assertEqual(payload, raw)

    def test_null_known_answer(self) -> None:
        self.assertEqual(der_null(), b"\x05\x00")

    def test_oid_known_answers(self) -> None:
        self.assertEqual(der_oid(OID_SHA256), b"\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01")
        self.assertEqual(
            der_oid(OID_RSA_ENCRYPTION), b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01"
        )
        self.assertEqual(der_oid(OID_DATA), b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x01")
        self.assertEqual(der_oid(OID_SIGNED_DATA), b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02")

    def test_oid_round_trip(self) -> None:
        for dotted in (OID_SHA256, OID_RSA_ENCRYPTION, OID_DATA, OID_SIGNED_DATA):
            tag, payload, _next = der_read_element(der_oid(dotted))
            self.assertEqual(tag, 0x06)
            self.assertEqual(der_oid_from_value(payload), dotted)

    def test_sequence_and_set_round_trip(self) -> None:
        item = der_integer(1)
        encoded_sequence = der_sequence(item, der_null())
        tag, payload, _next = der_read_element(encoded_sequence)
        self.assertEqual(tag, 0x30)  # SEQUENCE
        self.assertEqual(len(der_children(payload)), 2)
        encoded_set = der_set(item, der_oid(OID_SHA256))
        tag, payload, _next = der_read_element(encoded_set)
        self.assertEqual(tag, 0x31)  # SET
        self.assertEqual(len(der_children(payload)), 2)

    def test_set_sorts_children(self) -> None:
        # SET OF requires lexicographic DER order; the builder sorts.
        small = der_oid("1.2.3")
        large = der_oid("2.16.840.1.101.3.4.2.1")
        encoded = der_set(large, small)
        tag, payload, _next = der_read_element(encoded)
        children = der_children(payload)
        self.assertLess(children[0][1], children[1][1])

    def test_context_explicit_round_trip(self) -> None:
        inner = der_sequence(der_integer(1))
        encoded = der_context_explicit(0, inner)
        tag, payload, _next = der_read_element(encoded)
        self.assertEqual(tag, 0xA0)
        child_tag, _child_value, _ = der_read_element(payload)
        self.assertEqual(child_tag, 0x30)

    def test_long_length_form(self) -> None:
        raw = b"\x01" * 300
        encoded = der_octet_string(raw)
        tag, payload, _next = der_read_element(encoded)
        self.assertEqual(payload, raw)

    def test_algorithm_identifiers_known_answers(self) -> None:
        from engine.crypto import _algorithm_identifier

        self.assertEqual(_algorithm_identifier(OID_SHA256), SHA256_ALGORITHM)
        self.assertEqual(_algorithm_identifier(OID_RSA_ENCRYPTION), RSA_ALGORITHM)


class TestCMSSignedData(unittest.TestCase):
    """Parse the built CMS and verify structure + signature, pure Python."""

    def setUp(self) -> None:
        self.key = _make_key(1024)
        self.cms = build_cms_signed_data(
            DIGEST, self.key, signing_time=FIXED_TIME
        )

    def _signed_data(self):
        tag, value, _next = der_read_element(self.cms)
        self.assertEqual(tag, 0x30)  # ContentInfo
        children = der_children(value)
        self.assertEqual(der_oid_from_value(children[0][1]), OID_SIGNED_DATA)
        signed_data_wrapper = children[1]
        self.assertEqual(signed_data_wrapper[0], 0xA0)  # [0] EXPLICIT SignedData
        sd_tag, sd_value, _ = der_read_element(signed_data_wrapper[1])
        self.assertEqual(sd_tag, 0x30)  # SignedData SEQUENCE
        return der_children(sd_value)

    def test_content_info_structure(self) -> None:
        sd = self._signed_data()
        self.assertEqual(der_int_from_value(sd[0][1]), 1)  # version

    def test_digest_algorithms_is_sha256(self) -> None:
        sd = self._signed_data()
        algos = der_children(sd[1][1])
        self.assertEqual(len(algos), 1)
        algo = der_children(algos[0][1])
        self.assertEqual(der_oid_from_value(algo[0][1]), OID_SHA256)

    def test_encap_content_info_is_data(self) -> None:
        sd = self._signed_data()
        encap = der_children(sd[2][1])
        self.assertEqual(der_oid_from_value(encap[0][1]), OID_DATA)
        # Detached: no eContent [0] child.
        self.assertEqual(len(encap), 1)

    def test_signer_info_structure(self) -> None:
        sd = self._signed_data()
        signers = der_children(sd[3][1])
        self.assertEqual(len(signers), 1)
        fields = der_children(signers[0][1])
        self.assertEqual(der_int_from_value(fields[0][1]), 1)  # version
        # sid: issuerAndSerialNumber with the synthetic issuer CN.
        sid = der_children(fields[1][1])
        self.assertIn(b"pythoncoreengine", sid[0][1])
        self.assertEqual(der_int_from_value(sid[1][1]), 1)
        # digestAlgorithm = SHA-256.
        self.assertEqual(der_oid_from_value(der_children(fields[2][1])[0][1]), OID_SHA256)
        # signedAttrs is tag A0.
        self.assertEqual(fields[3][0], 0xA0)
        # signatureAlgorithm = rsaEncryption.
        self.assertEqual(
            der_oid_from_value(der_children(fields[4][1])[0][1]), OID_RSA_ENCRYPTION
        )
        # signature is an OCTET STRING of modulus length.
        self.assertEqual(fields[5][0], 0x04)
        self.assertEqual(len(fields[5][1]), self.key.size_bytes)

    def test_signed_attrs_authenticated_triple(self) -> None:
        sd = self._signed_data()
        fields = der_children(der_children(sd[3][1])[0][1])
        attrs = der_children(fields[3][1])
        oids = {}
        for attr in attrs:
            type_oid = der_oid_from_value(der_children(attr[1])[0][1])
            oids[type_oid] = attr
        self.assertEqual(
            set(oids), {OID_CONTENT_TYPE, OID_SIGNING_TIME, OID_MESSAGE_DIGEST}
        )
        # messageDigest attribute authenticates the content digest.
        md_attr = oids[OID_MESSAGE_DIGEST]
        md_value = der_children(md_attr[1])[1]  # SET OF child
        md_octets = der_children(md_value[1])[0][1]
        self.assertEqual(md_octets, DIGEST)
        # signingTime is the fixed UTCTime.
        st_attr = oids[OID_SIGNING_TIME]
        st_value = der_children(st_attr[1])[1]
        self.assertEqual(st_value[1], b"\x17\r260801120000Z")
        # contentType attribute is `data`.
        ct_attr = oids[OID_CONTENT_TYPE]
        ct_value = der_children(ct_attr[1])[1]
        self.assertEqual(der_oid_from_value(der_children(ct_value[1])[0][1]), OID_DATA)

    def test_signature_verifies_over_retagged_signed_attrs(self) -> None:
        sd = self._signed_data()
        fields = der_children(der_children(sd[3][1])[0][1])
        # The A0 field: tag 0xA0 whose content is the attribute encodings.
        self.assertEqual(fields[3][0], 0xA0)
        signature = fields[5][1]
        # RFC 5652 5.4: the signature covers the [0] IMPLICIT encoding of
        # the signedAttrs, i.e. the A0 TLV (reconstructed here with the
        # same length octets -- the SET and A0 wrappers share content).
        signed_attrs_tlv = der_encode_tlv(0xA0, fields[3][1])
        attrs_digest = hashlib.sha256(signed_attrs_tlv).digest()
        self.assertTrue(
            rsa_verify_pkcs1v15(self.key.public_key(), attrs_digest, signature)
        )
        # The raw content digest alone does not verify: the signature is
        # over the attributes, not over the document hash directly.
        self.assertFalse(
            rsa_verify_pkcs1v15(self.key.public_key(), DIGEST, signature)
        )

    def test_signed_attributes_input_retags_set(self) -> None:
        from engine.crypto import _signed_attributes

        attrs = _signed_attributes(DIGEST, FIXED_TIME)
        self.assertEqual(attrs[0], 0x31)
        retagged = signed_attributes_input(attrs)
        self.assertEqual(retagged[0], 0xA0)
        self.assertEqual(retagged[1:], attrs[1:])

    def test_deterministic(self) -> None:
        second = build_cms_signed_data(
            DIGEST, self.key, signing_time=FIXED_TIME
        )
        self.assertEqual(self.cms, second)

    def test_signature_round_trip_through_padding(self) -> None:
        # Sanity: the signature is a PKCS#1 v1.5 RSA signature; decode it
        # back through the public exponent and check the DigestInfo.
        sd = self._signed_data()
        fields = der_children(der_children(sd[3][1])[0][1])
        signature = fields[5][1]
        em = pow(
            int.from_bytes(signature, "big"), self.key.e, self.key.n
        ).to_bytes(self.key.size_bytes, "big")
        # The DigestInfo inside the padding is over the signedAttrs hash,
        # not the raw content digest.
        attrs_digest = hashlib.sha256(
            der_encode_tlv(0xA0, fields[3][1])
        ).digest()
        self.assertEqual(
            pkcs1_v1_5_decode(em),
            b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48"
            b"\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20" + attrs_digest,
        )

    def test_wrong_digest_length_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_cms_signed_data(b"short", self.key, signing_time=FIXED_TIME)


if __name__ == "__main__":
    unittest.main()

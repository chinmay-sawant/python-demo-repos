"""Pure-Python cryptography primitives (stdlib only): RSA, PKCS#1 v1.5, DER, CMS.

Phase 7.4 implements the full digital-signature pipeline with nothing but
the standard library: seeded RSA key generation (deterministic for tests),
RSA signing and verification over PKCS#1 v1.5 padded digests, a minimal
ASN.1 DER writer and a CMS/PKCS#7 ``SignedData`` builder.  There is no
``cryptography`` module and no openssl subprocess anywhere in this file.

Security disclaimer: the seeded key generation exists so tests and fixtures
are byte-deterministic; the keys it produces are NOT suitable for
production cryptography (``random.Random`` is not a CSPRNG, keys are not
certified, and no certificate chain is emitted).  The value of this module
is demonstrating the complete signature pipeline -- RSA math, DER layout,
byte-range hashing and CMS assembly -- in pure Python.

Design notes:

* RSA: ``n = p * q`` with ``e = 65537``; ``d = e^-1 mod lcm(p-1, q-1)``.
  Primality uses trial division by small primes followed by Miller-Rabin
  with bases drawn from the seeded PRNG (so the same seed always yields
  the same primes, hence the same key).
* PKCS#1 v1.5 (EMSA-PKCS1-v1_5, RFC 8017 9.2): the digest is wrapped in a
  ``DigestInfo`` SEQUENCE, padded to ``0x00 0x01 0xFF..0xFF 0x00``, then
  raised to the private exponent.  Verification strips the padding and
  compares the DigestInfo bytes.
* DER: a minimal writer covering exactly what CMS needs -- INTEGER,
  OCTET STRING, NULL, OBJECT IDENTIFIER, SEQUENCE, SET, UTCTime,
  PrintableString and explicit context tags.
* CMS: ``SignedData`` with version 1, SHA-256 digest algorithm, an
  ``encapContentInfo`` of type ``data`` (detached: no ``eContent``), and
  one ``SignerInfo`` whose ``sid`` is an ``IssuerAndSerialNumber`` with a
  synthetic issuer name.  The signer signs the DER encoding of the
  ``signedAttrs`` SET re-tagged as ``[0] IMPLICIT`` (the standard
  SignedAttributes trick), so the CMS carries the authenticated
  ``contentType`` / ``signingTime`` / ``messageDigest`` attributes needed
  for CAdES-style validation.  No X.509 certificate is embedded -- readers
  must trust the signer by other means (documented limitation).
"""

from __future__ import annotations

import datetime
import random
from typing import Dict, Optional, Sequence, Tuple

__all__ = [
    "OID_CONTENT_TYPE",
    "OID_DATA",
    "OID_MESSAGE_DIGEST",
    "OID_RSA_ENCRYPTION",
    "OID_SHA256",
    "OID_SIGNED_DATA",
    "OID_SIGNING_TIME",
    "RSAPrivateKey",
    "RSAPublicKey",
    "build_cms_signed_data",
    "der_context_explicit",
    "der_integer",
    "der_name",
    "der_null",
    "der_octet_string",
    "der_oid",
    "der_sequence",
    "der_set",
    "der_utctime",
    "generate_rsa_key",
    "pkcs1_v1_5_decode",
    "pkcs1_v1_5_encode",
    "rsa_public_from_private",
    "rsa_sign_pkcs1v15",
    "rsa_verify_pkcs1v15",
    "sha256_digest_info",
]

# ---------------------------------------------------------------------------
# ASN.1 object identifiers (dotted-decimal form)
# ---------------------------------------------------------------------------

#: PKCS#7 ``signedData`` content type.
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
#: PKCS#7 ``data`` content type (the encapContentInfo type for PDF).
OID_DATA = "1.2.840.113549.1.7.1"
#: SHA-256 digest algorithm (RFC 5754).
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
#: rsaEncryption signature algorithm (PKCS#1).
OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
#: PKCS#9 ``contentType`` signed attribute.
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
#: PKCS#9 ``messageDigest`` signed attribute.
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
#: PKCS#9 ``signingTime`` signed attribute.
OID_SIGNING_TIME = "1.2.840.113549.1.9.5"
#: X.520 ``commonName`` attribute type (for the synthetic issuer).
OID_COMMON_NAME = "2.5.4.3"

#: The SHA-256 DigestInfo prefix (RFC 8017 Appendix A.2.4): the DER
#: encoding of ``SEQUENCE { AlgorithmIdentifier(sha256, NULL), OCTET STRING }``
#: with an empty digest.  The actual DigestInfo is this prefix plus the
#: 32 digest bytes, all inside one outer SEQUENCE.
_SHA256_DIGEST_INFO_PREFIX = (
    b"\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20"
)

#: Small primes for trial division before Miller-Rabin.
_SMALL_PRIMES = (
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67,
    71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149,
    151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229,
    233, 239, 241, 251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313,
    317, 331, 337, 347, 349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409,
    419, 421, 431, 433, 439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499,
    503, 509, 521, 523, 541, 547, 557, 563, 569, 571, 577, 587, 593, 599, 601,
    607, 613, 617, 619, 631, 641, 643, 647, 653, 659, 661, 673, 677, 683, 691,
    701, 709, 719, 727, 733, 739, 743, 751, 757, 761, 769, 773, 787, 797, 809,
    811, 821, 823, 827, 829, 839, 853, 857, 859, 863, 877, 881, 883, 887, 907,
    911, 919, 929, 937, 941, 947, 953, 967, 971, 977, 983, 991, 997,
)

#: A module-level key cache: deterministic keys are pure functions of
#: ``(bits, seed)``, so generating the same key twice (e.g. a fixture built
#: in two tests) must not pay the prime search twice and must not risk
#: non-deterministic re-generation.
_KEY_CACHE: Dict[Tuple[int, int], "RSAPrivateKey"] = {}


# ---------------------------------------------------------------------------
# RSA
# ---------------------------------------------------------------------------


class RSAPublicKey:
    """An RSA public key (``n`` modulus, ``e`` public exponent)."""

    __slots__ = ("e", "n")

    def __init__(self, n: int, e: int) -> None:
        self.n = n
        self.e = e

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, RSAPublicKey)
            and self.n == other.n
            and self.e == other.e
        )

    def __hash__(self) -> int:
        return hash((self.n, self.e))

    @property
    def size_bytes(self) -> int:
        """The modulus byte length (must equal the signature byte length)."""
        return (self.n.bit_length() + 7) // 8


class RSAPrivateKey:
    """An RSA private key: modulus, exponents and primes.

    ``size_bytes`` is the byte length of the modulus (the signature
    length).  The key is generated by :func:`generate_rsa_key`; parsing
    external PEM/DER keys is out of scope (documented limitation).
    """

    __slots__ = ("d", "e", "n", "p", "q")

    def __init__(self, n: int, e: int, d: int, p: int, q: int) -> None:
        self.n = n
        self.e = e
        self.d = d
        self.p = p
        self.q = q

    @property
    def size_bytes(self) -> int:
        """The modulus byte length; also the signature byte length."""
        return (self.n.bit_length() + 7) // 8

    def public_key(self) -> RSAPublicKey:
        """The matching public key (for verification and tests)."""
        return RSAPublicKey(self.n, self.e)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, RSAPrivateKey)
            and (self.n, self.e, self.d) == (other.n, other.e, other.d)
        )


def rsa_public_from_private(key: RSAPrivateKey) -> RSAPublicKey:
    """The public half of ``key``."""
    return key.public_key()


def _miller_rabin(n: int, bases: Sequence[int]) -> bool:
    """True when ``n`` passes Miller-Rabin for every base in ``bases``."""
    if n < 2:
        return False
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for base in bases:
        a = base % n
        if a == 0:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int, rng: random.Random) -> int:
    """A ``bits``-bit prime chosen by the seeded ``rng`` (deterministic)."""
    assert bits >= 16
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if candidate % 3 == 0 or candidate % 5 == 0 or candidate % 7 == 0:
            continue
        divisible = False
        for prime in _SMALL_PRIMES[4:]:
            if candidate % prime == 0:
                divisible = True
                break
        if divisible:
            continue
        # Miller-Rabin with 20 bases drawn from the seeded stream; the
        # sequence is deterministic per seed, so the "prime" found is too.
        bases = [rng.randrange(2, candidate - 1) for _ in range(20)]
        if _miller_rabin(candidate, bases):
            return candidate
    raise AssertionError("unreachable")


def generate_rsa_key(bits: int = 2048, seed: Optional[int] = None) -> RSAPrivateKey:
    """Generate an RSA key pair deterministically from ``seed``.

    ``bits`` is the modulus size (2048 by default; 1024 keeps tests fast).
    Keys are cached per ``(bits, seed)`` so repeated generation is free and
    byte-stable.  NOT for production security -- see the module docstring.
    """
    if bits < 512:
        raise ValueError("RSA modulus must be at least 512 bits")
    key = _KEY_CACHE.get((bits, seed))
    if key is not None:
        return key
    rng = random.Random(seed)
    half = bits // 2
    e = 65537
    while True:
        p = _random_prime(half, rng)
        q = _random_prime(half, rng)
        if q == p:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue  # keep the modulus exactly ``bits`` bits
        lam = (p - 1) * (q - 1)  # lcm(p-1, q-1) with p != q: (p-1)(q-1)/gcd
        g = _mod_gcd(p - 1, q - 1)
        lam //= g
        try:
            d = pow(e, -1, lam)
        except ValueError:
            continue
        key = RSAPrivateKey(n, e, d, p, q)
        _KEY_CACHE[(bits, seed)] = key
        return key


def _mod_gcd(a: int, b: int) -> int:
    """Euclid's gcd (small helper so the lcm exponent is exact)."""
    while b:
        a, b = b, a % b
    return a


# ---------------------------------------------------------------------------
# PKCS#1 v1.5 encoding (EMSA-PKCS1-v1_5, RFC 8017 9.2)
# ---------------------------------------------------------------------------


def sha256_digest_info(digest: bytes) -> bytes:
    """The DER DigestInfo for a 32-byte SHA-256 ``digest``."""
    if len(digest) != 32:
        raise ValueError(f"SHA-256 digests are 32 bytes, got {len(digest)}")
    return _SHA256_DIGEST_INFO_PREFIX + digest


def pkcs1_v1_5_encode(digest: bytes, em_len: int) -> bytes:
    """EMSA-PKCS1-v1_5: pad ``digest`` into an ``em_len``-byte encoded message."""
    digest_info = sha256_digest_info(digest)
    if em_len < len(digest_info) + 11:
        raise ValueError("encoded message too short for SHA-256 DigestInfo")
    padding = b"\xff" * (em_len - len(digest_info) - 3)
    return b"\x00\x01" + padding + b"\x00" + digest_info


def pkcs1_v1_5_decode(em: bytes) -> bytes:
    """EMSA-PKCS1-v1_5 verify+decode: return the DigestInfo or raise ValueError."""
    if len(em) < 11 or em[0:2] != b"\x00\x01":
        raise ValueError("bad PKCS#1 v1.5 padding: wrong header")
    separator = em.find(b"\x00", 2)
    if separator < 10:
        raise ValueError("bad PKCS#1 v1.5 padding: no 0x00 separator")
    if em[2:separator] != b"\xff" * (separator - 2):
        raise ValueError("bad PKCS#1 v1.5 padding: non-0xff fill")
    return em[separator + 1 :]


def _rsa_encrypt_int(value: int, key_n: int, key_e: int) -> int:
    """Raw RSA primitive: ``value ** key_e mod key_n``."""
    return pow(value, key_e, key_n)


def rsa_sign_pkcs1v15(key: RSAPrivateKey, digest: bytes) -> bytes:
    """RSA-SHA256 sign: PKCS#1 v1.5 pad ``digest``, then raise to ``d``.

    Returns the signature as ``key.size_bytes`` bytes (big-endian, zero
    padded to the modulus length), which is what the CMS ``signature``
    OCTET STRING carries.
    """
    em = pkcs1_v1_5_encode(digest, key.size_bytes)
    em_int = int.from_bytes(em, "big")
    sig_int = _rsa_encrypt_int(em_int, key.n, key.d)
    return sig_int.to_bytes(key.size_bytes, "big")


def rsa_verify_pkcs1v15(key: RSAPublicKey, digest: bytes, signature: bytes) -> bool:
    """Verify an RSA-SHA256 PKCS#1 v1.5 ``signature`` over ``digest``."""
    if len(signature) != key.size_bytes:
        return False
    em_int = _rsa_encrypt_int(int.from_bytes(signature, "big"), key.n, key.e)
    em = em_int.to_bytes(key.size_bytes, "big")
    try:
        got = pkcs1_v1_5_decode(em)
    except ValueError:
        return False
    return got == sha256_digest_info(digest)


# ---------------------------------------------------------------------------
# Minimal DER writer (ASN.1 distinguished encoding rules, tag/length/value)
# ---------------------------------------------------------------------------


def _der_length(n: int) -> bytes:
    """DER length octets for a payload of ``n`` bytes."""
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der_tlv(tag: int, payload: bytes) -> bytes:
    """Encode one tag/length/value triplet."""
    return bytes([tag]) + _der_length(len(payload)) + payload


def der_integer(value: int) -> bytes:
    """DER INTEGER (non-negative values only; a leading 0x00 is added when
    the high bit would otherwise set the sign bit)."""
    if value < 0:
        raise ValueError("negative INTEGERs are not needed for CMS")
    if value == 0:
        return b"\x02\x01\x00"
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _der_tlv(0x02, raw)


def der_octet_string(data: bytes) -> bytes:
    """DER OCTET STRING."""
    return _der_tlv(0x04, data)


def der_null() -> bytes:
    """DER NULL (the empty parameter of SHA-256 / rsaEncryption)."""
    return b"\x05\x00"


def der_oid(dotted: str) -> bytes:
    """DER OBJECT IDENTIFIER from a dotted-decimal string."""
    arcs = [int(arc) for arc in dotted.split(".")]
    if len(arcs) < 2 or arcs[0] > 2 or arcs[0] < 0:
        raise ValueError(f"unsupported OID {dotted!r}")
    first = 40 * arcs[0] + arcs[1]
    body = bytearray([first])
    for arc in arcs[2:]:
        if arc < 0:
            raise ValueError(f"unsupported OID {dotted!r}")
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        chunk.reverse()
        body.extend(chunk)
    return _der_tlv(0x06, bytes(body))


def der_sequence(*items: bytes) -> bytes:
    """DER SEQUENCE over the encoded ``items``."""
    return _der_tlv(0x30, b"".join(items))


def der_set(*items: bytes) -> bytes:
    """DER SET OF: items sorted by their full DER encodings (required for
    SET OF canonical ordering)."""
    return _der_tlv(0x31, b"".join(sorted(items)))


def der_context_explicit(tag_number: int, payload: bytes) -> bytes:
    """DER ``[tag_number] EXPLICIT`` constructed context tag (0xA0, 0xA1, ...)."""
    return _der_tlv(0xA0 + tag_number, payload)


def der_utctime(when: datetime.datetime) -> bytes:
    """DER UTCTime: ``YYMMDDHHMMSSZ`` (UTC, no fractional seconds).

    Naive datetimes are interpreted as UTC, so the same input yields the
    same bytes on any machine timezone (test determinism).
    """
    if when.tzinfo is None:
        utc = when.replace(tzinfo=datetime.timezone.utc)
    else:
        utc = when.astimezone(datetime.timezone.utc)
    return _der_tlv(0x17, utc.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def der_printable_string(text: str) -> bytes:
    """DER PrintableString (the synthetic issuer's CN must be printable)."""
    return _der_tlv(0x13, text.encode("ascii"))


def der_name(common_name: str) -> bytes:
    """A minimal X.501 ``Name``: one RDN with a PrintableString CN.

    This is a synthetic issuer for the ``IssuerAndSerialNumber`` signer
    identifier; it does not correspond to any real certificate authority.
    """
    rdn = der_set(der_sequence(der_oid(OID_COMMON_NAME), der_printable_string(common_name)))
    return der_sequence(rdn)


# ---------------------------------------------------------------------------
# CMS / PKCS#7 SignedData
# ---------------------------------------------------------------------------

#: The default signing subject used in the synthetic issuer name.
DEFAULT_SIGNER_NAME = "pythoncoreengine test signer"

#: The default value of the ``/Name`` entry on the signature dictionary.
DEFAULT_SIGNATURE_NAME = "pythoncoreengine"


def _algorithm_identifier(oid: str) -> bytes:
    """AlgorithmIdentifier with NULL parameters (SHA-256, rsaEncryption)."""
    return der_sequence(der_oid(oid), der_null())


def _attribute(oid: str, value: bytes) -> bytes:
    """A PKCS#9 Attribute: ``SEQUENCE { type OID, values SET OF value }``."""
    return der_sequence(der_oid(oid), der_set(value))


def _signed_attributes(
    content_digest: bytes,
    signing_time: datetime.datetime,
    content_type_oid: str = OID_DATA,
) -> bytes:
    """The authenticated signedAttrs SET (contentType, signingTime, messageDigest).

    The signer's message digest is ``content_digest`` (for PDF: the
    SHA-256 over the two /ByteRange slices).  The returned bytes are the
    plain SET encoding; the signature input is this encoding with the SET
    tag (0x31) replaced by the SignedAttributes context tag (0xA0).
    """
    attrs = [
        _attribute(OID_CONTENT_TYPE, der_oid(content_type_oid)),
        _attribute(OID_SIGNING_TIME, der_utctime(signing_time)),
        _attribute(OID_MESSAGE_DIGEST, der_octet_string(content_digest)),
    ]
    return der_set(*attrs)


def signed_attributes_input(attributes_set: bytes) -> bytes:
    """The signature input for a signedAttrs SET: retag SET (0x31) as A0.

    RFC 5652 5.4: the signature is computed over the DER encoding of the
    attributes with the SET OF tag replaced by ``[0] IMPLICIT`` (0xA0).
    The length octets are unchanged because the tag byte alone differs.
    """
    if not attributes_set or attributes_set[0] != 0x31:
        raise ValueError("signed attributes must be a DER SET")
    return b"\xa0" + attributes_set[1:]


def build_cms_signed_data(
    content_digest: bytes,
    key: RSAPrivateKey,
    *,
    signing_time: Optional[datetime.datetime] = None,
    signer_name: str = DEFAULT_SIGNER_NAME,
    serial_number: int = 1,
) -> bytes:
    """Build a DER CMS/PKCS#7 ``SignedData`` ContentInfo signed by ``key``.

    Args:
        content_digest: the 32-byte SHA-256 digest the ``messageDigest``
            signed attribute authenticates (for PDF: the SHA-256 over the
            two /ByteRange slices).  This is the *document* digest; the
            RSA signature itself covers the signedAttrs encoding (RFC
            5652 5.4), computed internally.
        key: the RSA private key (engine.crypto) whose signature the CMS
            carries.
        signing_time: the signingTime attribute (defaults to now); pass a
            fixed value for deterministic output.
        signer_name: the synthetic issuer CN of the signer identifier.
        serial_number: the synthetic issuer serial number.

    Returns:
        The complete ContentInfo bytes (``SEQUENCE { signedData OID,
        [0] EXPLICIT SignedData }``) ready for the signature dictionary's
        ``/Contents`` hex string.
    """
    import hashlib

    if len(content_digest) != 32:
        raise ValueError(f"SHA-256 digests are 32 bytes, got {len(content_digest)}")
    when = signing_time if signing_time is not None else datetime.datetime.now()
    digest_algorithm = _algorithm_identifier(OID_SHA256)
    attributes = _signed_attributes(content_digest, when)
    # The signedAttrs field of SignerInfo is [0] IMPLICIT SignedAttributes:
    # a constructed context tag 0xA0 whose content octets are exactly the
    # SET's content octets, so the field encoding is the SET encoding with
    # its tag byte swapped -- the same bytes the signature covers.
    signed_attrs_encoding = signed_attributes_input(attributes)
    signature = rsa_sign_pkcs1v15(key, hashlib.sha256(signed_attrs_encoding).digest())

    encap_content_info = der_sequence(
        der_oid(OID_DATA),
        # detached signature: no eContent -- the messageDigest attribute
        # carries the hash of the /ByteRange slices.
    )

    signer_info = der_sequence(
        der_integer(1),  # version
        der_sequence(  # issuerAndSerialNumber
            der_name(signer_name), der_integer(serial_number)
        ),
        digest_algorithm,
        signed_attrs_encoding,  # signedAttrs: [0] IMPLICIT SET
        _algorithm_identifier(OID_RSA_ENCRYPTION),
        der_octet_string(signature),
    )

    signed_data = der_sequence(
        der_integer(1),  # version
        der_set(digest_algorithm),  # digestAlgorithms
        encap_content_info,
        der_set(signer_info),  # signerInfos
    )
    return der_sequence(der_oid(OID_SIGNED_DATA), der_context_explicit(0, signed_data))

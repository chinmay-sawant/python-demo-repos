"""PDF standard security handler: encryption and decryption (stdlib only).

Phase 7.5 implements the /Standard security handler for the two AES
revisions the engine supports:

* Revision 4 / V 4 (``/CFM /AESV2``, 128-bit): the file key is derived
  from the user password by Algorithm 2 (MD5 over the padded password,
  /O, /P and the first /ID element, iterated 50 times); /O and /U are
  built from the owner/user passwords with the 20-round RC4 scheme; every
  string and stream is encrypted with AES-128-CBC under an object key
  ``MD5(file_key + objnum + gen + "sAlT")[:16]``.
* Revision 6 / V 5 (``/CFM /AESV3``, 256-bit): the file key is a random
  32-byte value stored in the document, encrypted under password-derived
  intermediate keys (/UE and /OE).  Password hashing uses ISO 32000-2
  Algorithm 2.B -- SHA-256 over password+salt, then 64+ rounds of
  AES-CBC + SHA-2 where the hash function is chosen from the first 16
  bytes of the round output ("remainder rounding": rounds continue while
  ``count < 64 or count < e[-1] + 32``).  The object key for AES-256 is
  the file key itself (Algorithm 3.1a, no MD5 derivation).

Both revisions encrypt strings and streams by object number.  Strings are
emitted as hex strings (the engine's choice -- the spec allows either
form), streams keep their dictionary with a rewritten ``/Length``
(ciphertext length on disk, plaintext length after decryption), and the
xref section, trailer and the /Encrypt dictionary itself are never
encrypted.

Determinism: :class:`EncryptSpec` carries a ``seed`` that drives every
random choice (salts, file key, per-string IVs) so fixtures and tests are
byte-stable across runs.  Production use passes ``seed=None``, which
falls back to ``os.urandom`` for each choice -- this encryption is
intended to be correct and interoperable, not a security product.

The pure-Python decryptor :func:`decrypt_pdf` parses the generated PDF,
recovers the file key from the password (user or owner), decrypts every
string and stream and rebuilds the document byte-for-byte identically to
the unencrypted render (xref offsets are recomputed from the restored
bodies).
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cipher import (
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    pkcs7_pad,
    pkcs7_unpad,
    rc4,
)
from .write import (
    B,
    N,
    ObjectId,
    PdfHexString,
    PdfName,
    encode_dict,
    encode_hex_string,
    encode_object,
    encode_string,
    encode_xref_section,
)

__all__ = [
    "ALL_PERMISSIONS",
    "EncryptSpec",
    "PERM_ANNOTATE",
    "PERM_ASSEMBLE",
    "PERM_COPY",
    "PERM_EXTRACT",
    "PERM_FILL_FORMS",
    "PERM_MODIFY",
    "PERM_PRINT_HIGH",
    "PERM_PRINT_LOW",
    "StandardSecurityHandler",
    "WrongPasswordError",
    "decrypt_pdf",
    "encrypt_pdf",
    "permission_flags",
]

# ---------------------------------------------------------------------------
# Permissions (ISO 32000-2 Table 22, the /P bit flags)
# ---------------------------------------------------------------------------

PERM_PRINT_LOW = 0x04  # print at low resolution (bit 2)
PERM_MODIFY = 0x08  # modify contents (bit 3)
PERM_COPY = 0x10  # copy text/images (bit 4)
PERM_ANNOTATE = 0x20  # add/change annotations and form fields (bit 5)
PERM_FILL_FORMS = 0x100  # fill existing form fields (bit 8)
PERM_EXTRACT = 0x200  # extract text/graphics for accessibility (bit 9)
PERM_ASSEMBLE = 0x400  # assemble document (bit 10)
PERM_PRINT_HIGH = 0x800  # print at full resolution (bit 11)

#: Every permission bit set (the conventional "all permissions" value).
ALL_PERMISSIONS = 0xFFFFFFFC


def permission_flags(
    *,
    print_low: bool = True,
    modify: bool = True,
    copy: bool = True,
    annotate: bool = True,
    fill_forms: bool = True,
    extract: bool = True,
    assemble: bool = True,
    print_high: bool = True,
) -> int:
    """Build the /P integer from named permission bits (all on by default)."""
    flags = 0
    if print_low:
        flags |= PERM_PRINT_LOW
    if modify:
        flags |= PERM_MODIFY
    if copy:
        flags |= PERM_COPY
    if annotate:
        flags |= PERM_ANNOTATE
    if fill_forms:
        flags |= PERM_FILL_FORMS
    if extract:
        flags |= PERM_EXTRACT
    if assemble:
        flags |= PERM_ASSEMBLE
    if print_high:
        flags |= PERM_PRINT_HIGH
    return flags | 0xFFFFFFC0  # reserved bits 6-7 and 12-31 stay set


# ---------------------------------------------------------------------------
# Public configuration
# ---------------------------------------------------------------------------


@dataclass
class EncryptSpec:
    """The document encryption configuration passed to the builder.

    ``password`` is the user password (empty string means "no user
    password").  ``owner_password`` defaults to the user password when
    omitted.  ``revision`` selects the handler: 4 (AES-128, /AESV2) or 6
    (AES-256, /AESV3).  ``permissions`` is the /P bit field (see
    :func:`permission_flags`).

    ``seed`` makes every random choice deterministic (salts, the AES-256
    file key and every string/stream IV), which is what keeps the
    encrypted fixtures byte-identical across runs.  Pass ``None`` in
    production for ``os.urandom``-based randomness.
    """

    password: str = ""
    owner_password: Optional[str] = None
    permissions: int = ALL_PERMISSIONS
    revision: int = 4
    encrypt_metadata: bool = True
    seed: Optional[bytes] = None

    def __post_init__(self) -> None:
        if self.revision not in (4, 6):
            raise ValueError(f"revision must be 4 or 6, got {self.revision}")


class WrongPasswordError(ValueError):
    """The supplied password is neither the user nor the owner password."""


# ---------------------------------------------------------------------------
# Password encoding and the classic 32-byte padding (Algorithm 2 step a)
# ---------------------------------------------------------------------------

_PAD32 = bytes(
    (
        0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41,
        0x64, 0x00, 0x4E, 0x56, 0xFF, 0xFA, 0x01, 0x08,
        0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
        0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A,
    )
)


def _pad_password_32(password: bytes) -> bytes:
    """Pad or truncate a password to exactly 32 bytes (Algorithm 2 step a)."""
    return (password + _PAD32)[:32]


def _encode_password_r4(password: str) -> bytes:
    """The revision-4 password bytes (PDFDocEncoding; ASCII/latin-1 here).

    The full PDFDocEncoding mapping differs from latin-1 in a handful of
    code points; ASCII passwords (the engine's documented scope) are
    byte-identical under both, so latin-1 is a safe stand-in.
    """
    try:
        return password.encode("ascii")
    except UnicodeEncodeError:
        return password.encode("latin-1")


def _encode_password_r6(password: str) -> bytes:
    """The revision-6 password bytes: UTF-8 (SASLprep), truncated to 127."""
    return password.encode("utf-8")[:127]


# ---------------------------------------------------------------------------
# Revision 4 (AES-128) algorithms
# ---------------------------------------------------------------------------


def _md5_iterate(data: bytes, iterations: int, length: int) -> bytes:
    """Algorithm 2 step h: ``iterations`` rounds of MD5 over ``length`` bytes."""
    digest = data
    for _ in range(iterations):
        digest = hashlib.md5(digest[:length]).digest()
    return digest


def _rc4_20(data: bytes, key: bytes) -> bytes:
    """Algorithm 3/4 step: 20 RC4 rounds, key XORed with 0..19 each round."""
    encrypted = rc4(key, data)
    for i in range(1, 20):
        encrypted = rc4(bytes(byte ^ i for byte in key), encrypted)
    return encrypted


def _compute_o_r4(owner_password: bytes, user_password: bytes) -> bytes:
    """Algorithm 3: the /O entry from the owner password (RC4, 20 rounds)."""
    owner_key = hashlib.md5(_pad_password_32(owner_password)).digest()
    owner_key = _md5_iterate(owner_key, 50, 16)
    return _rc4_20(_pad_password_32(user_password), owner_key)


def _compute_key_r4(
    user_password: bytes,
    o_entry: bytes,
    permissions: int,
    id0: bytes,
    encrypt_metadata: bool,
) -> bytes:
    """Algorithm 2: the 16-byte file encryption key from the user password."""
    digest = hashlib.md5()
    digest.update(_pad_password_32(user_password))
    digest.update(o_entry)
    digest.update(struct.pack("<I", permissions & 0xFFFFFFFF))
    digest.update(id0)
    if not encrypt_metadata:
        digest.update(b"\xff\xff\xff\xff")
    return _md5_iterate(digest.digest(), 50, 16)


def _compute_u_r4(key: bytes, user_password: bytes, id0: bytes) -> bytes:
    """Algorithm 5: the /U entry (16 RC4 bytes + 16 padding bytes)."""
    u_hash = hashlib.md5()
    u_hash.update(_PAD32)
    u_hash.update(id0)
    u16 = _rc4_20(u_hash.digest(), key)
    return u16 + _PAD32[:16]


def _recover_user_from_owner_r4(
    owner_password: bytes, o_entry: bytes
) -> Optional[bytes]:
    """Algorithm 7: decrypt the user password out of /O (None on mismatch-proof failure)."""
    owner_key = hashlib.md5(_pad_password_32(owner_password)).digest()
    owner_key = _md5_iterate(owner_key, 50, 16)
    user_password = o_entry
    for i in range(19, -1, -1):
        user_password = rc4(bytes(byte ^ i for byte in owner_key), user_password)
    return user_password


# ---------------------------------------------------------------------------
# Revision 6 (AES-256) algorithms
# ---------------------------------------------------------------------------

_SHA2_FUNCTIONS = (hashlib.sha256, hashlib.sha384, hashlib.sha512)


def _hardened_hash_r6(password: bytes, salt: bytes, udata: bytes) -> bytes:
    """Algorithm 2.B: the hardened SHA-2/AES-256 password hash.

    ``udata`` is the 48-byte /U value when hashing the owner password and
    empty for the user password / file key.  At least 64 rounds run; the
    loop continues past 64 while the last ciphertext byte demands it
    (``e[-1] <= count - 32``), the "remainder rounding".
    """
    k = hashlib.sha256(password + salt + udata).digest()
    count = 0
    while True:
        count += 1
        k1 = password + k + udata
        e = aes_cbc_encrypt(k[:16], k[16:32], k1 * 64)
        k = _SHA2_FUNCTIONS[sum(e[:16]) % 3](e).digest()
        if count >= 64 and e[-1] <= count - 32:
            break
    return k[:32]


# ---------------------------------------------------------------------------
# The security handler
# ---------------------------------------------------------------------------


class StandardSecurityHandler:
    """Implements the /Standard handler for revisions 4 and 6.

    Constructed either from an :class:`EncryptSpec` (the writer path,
    :meth:`create`) or from a parsed /Encrypt dictionary (the reader
    path, :meth:`load`).  The reader path recovers the file key via
    :meth:`recover_key`, which raises :class:`WrongPasswordError` for a
    bad password.
    """

    def __init__(
        self,
        *,
        rev: int,
        v: int,
        length_bits: int,
        cfm: str,
        o: bytes,
        u: bytes,
        p: int,
        id0: bytes,
        encrypt_metadata: bool,
        key: Optional[bytes],
        ue: Optional[bytes] = None,
        oe: Optional[bytes] = None,
        perms: Optional[bytes] = None,
        seed: Optional[bytes] = None,
    ) -> None:
        if key is not None and len(key) not in (16, 32):
            raise ValueError(f"invalid file key length {len(key)}")
        self._rev = rev
        self._v = v
        self._length_bits = length_bits
        self._cfm = cfm
        self._o = o
        self._u = u
        self._p = p & 0xFFFFFFFF
        self._id0 = id0
        self._encrypt_metadata = encrypt_metadata
        self._key = key
        self._ue = ue
        self._oe = oe
        self._perms = perms
        self._seed = seed

    # -- construction ----------------------------------------------------

    @classmethod
    def create(cls, spec: EncryptSpec, id0: bytes) -> "StandardSecurityHandler":
        """Build the handler for a fresh document (all /O /U /UE /OE /Perms)."""
        if spec.revision == 4:
            user = _encode_password_r4(spec.password)
            owner = _encode_password_r4(
                spec.owner_password if spec.owner_password is not None else spec.password
            )
            o = _compute_o_r4(owner, user)
            key = _compute_key_r4(
                user, o, spec.permissions, id0, spec.encrypt_metadata
            )
            u = _compute_u_r4(key, user, id0)
            return cls(
                rev=4,
                v=4,
                length_bits=128,
                cfm="AESV2",
                o=o,
                u=u,
                p=spec.permissions,
                id0=id0,
                encrypt_metadata=spec.encrypt_metadata,
                key=key,
                seed=spec.seed,
            )
        if spec.revision == 6:
            user = _encode_password_r6(spec.password)
            owner = _encode_password_r6(
                spec.owner_password if spec.owner_password is not None else spec.password
            )
            rnd = _RandomSource(spec.seed)
            file_key = rnd.file_key()
            user_val, user_key = rnd.salts(b"user-val"), rnd.salts(b"user-key")
            u_hash = _hardened_hash_r6(user, user_val, b"")
            u = u_hash + user_val + user_key
            ue = aes_cbc_encrypt(_hardened_hash_r6(user, user_key, b""), _ZERO_IV, file_key)
            owner_val, owner_key = rnd.salts(b"owner-val"), rnd.salts(b"owner-key")
            o_hash = _hardened_hash_r6(owner, owner_val, u)
            o = o_hash + owner_val + owner_key
            oe = aes_cbc_encrypt(
                _hardened_hash_r6(owner, owner_key, u), _ZERO_IV, file_key
            )
            perms = _compute_perms(file_key, spec.permissions, spec.encrypt_metadata)
            return cls(
                rev=6,
                v=5,
                length_bits=256,
                cfm="AESV3",
                o=o,
                u=u,
                p=spec.permissions,
                id0=id0,
                encrypt_metadata=spec.encrypt_metadata,
                key=file_key,
                ue=ue,
                oe=oe,
                perms=perms,
                seed=spec.seed,
            )
        raise ValueError(f"unsupported revision {spec.revision}")

    @classmethod
    def load(
        cls, entries: Dict[str, Any], id0: bytes
    ) -> "StandardSecurityHandler":
        """Build the handler from a parsed /Encrypt dictionary (reader path).

        The file key is unknown until :meth:`recover_key` runs.
        """
        v = entries["V"]
        cfm = entries["CFM"]
        if v >= 5:
            if cfm != "AESV3":
                raise ValueError(f"unsupported crypt filter {cfm!r} for V {v}")
            return cls(
                rev=entries["R"],
                v=v,
                length_bits=entries["Length"],
                cfm=cfm,
                o=entries["O"],
                u=entries["U"],
                p=entries["P"],
                id0=id0,
                encrypt_metadata=entries["EncryptMetadata"],
                key=None,
                ue=entries["UE"],
                oe=entries["OE"],
                perms=entries["Perms"],
            )
        if cfm != "AESV2":
            raise ValueError(f"unsupported crypt filter {cfm!r} for V {v}")
        return cls(
            rev=entries["R"],
            v=v,
            length_bits=entries["Length"],
            cfm=cfm,
            o=entries["O"],
            u=entries["U"],
            p=entries["P"],
            id0=id0,
            encrypt_metadata=entries["EncryptMetadata"],
            key=None,
        )

    # -- the /Encrypt dictionary ------------------------------------------

    def encrypt_dict(self) -> Dict[Any, Any]:
        """The /Encrypt dictionary body (stored as an indirect object)."""
        cf_length = 16 if self._v < 5 else 32
        cfm = N("AESV2") if self._v < 5 else N("AESV3")
        d: Dict[Any, Any] = {
            N("Filter"): N("Standard"),
            N("V"): self._v,
            N("R"): self._rev,
            N("Length"): self._length_bits,
            N("CF"): {
                N("StdCF"): {
                    N("CFM"): cfm,
                    N("AuthEvent"): N("DocOpen"),
                    N("Length"): cf_length,
                }
            },
            N("StmF"): N("StdCF"),
            N("StrF"): N("StdCF"),
            N("EncryptMetadata"): B(self._encrypt_metadata),
            N("O"): PdfHexString(self._o),
            N("U"): PdfHexString(self._u),
            N("P"): self._p,
        }
        if self._v >= 5:
            d[N("UE")] = PdfHexString(self._ue)
            d[N("OE")] = PdfHexString(self._oe)
            d[N("Perms")] = PdfHexString(self._perms)
        return d

    # -- password verification / key recovery ------------------------------

    def recover_key(self, password: str) -> bytes:
        """Recover the file encryption key; raises :class:`WrongPasswordError`."""
        if self._v >= 5:
            key = self._recover_key_r6(password)
        else:
            key = self._recover_key_r4(password)
        self._key = key
        return key

    def _recover_key_r4(self, password: str) -> bytes:
        candidate = _encode_password_r4(password)
        key = _compute_key_r4(
            candidate, self._o, self._p, self._id0, self._encrypt_metadata
        )
        if _rc4_20(hashlib.md5(_PAD32 + self._id0).digest(), key)[:16] == self._u[:16]:
            return key
        recovered = _recover_user_from_owner_r4(candidate, self._o)
        user_key = _compute_key_r4(
            recovered, self._o, self._p, self._id0, self._encrypt_metadata
        )
        if _rc4_20(hashlib.md5(_PAD32 + self._id0).digest(), user_key)[:16] == self._u[:16]:
            return user_key
        raise WrongPasswordError("wrong password for encrypted document")

    def _recover_key_r6(self, password: str) -> bytes:
        candidate = _encode_password_r6(password)
        if _hardened_hash_r6(candidate, self._u[32:40], b"") == self._u[:32]:
            intermediate = _hardened_hash_r6(candidate, self._u[40:48], b"")
            key = aes_cbc_decrypt(intermediate, _ZERO_IV, self._ue)
        elif _hardened_hash_r6(candidate, self._o[32:40], self._u) == self._o[:32]:
            intermediate = _hardened_hash_r6(candidate, self._o[40:48], self._u)
            key = aes_cbc_decrypt(intermediate, _ZERO_IV, self._oe)
        else:
            raise WrongPasswordError("wrong password for encrypted document")
        perms_plain = aes_cbc_decrypt(key, _ZERO_IV, self._perms)
        if perms_plain[9:12] != b"adb":
            raise WrongPasswordError("permissions check failed")
        return key

    # -- per-object keys and data transforms -------------------------------

    def object_key(self, objnum: int, gen: int = 0) -> bytes:
        """Algorithm 1 / 3.1a: the key for one object's strings and streams."""
        if self._v >= 5:
            return self._key
        if self._key is None:
            raise ValueError("file key not recovered yet")
        digest = hashlib.md5()
        digest.update(self._key)
        digest.update(struct.pack("<I", objnum)[:3])
        digest.update(struct.pack("<I", gen)[:2])
        digest.update(b"sAlT")
        return digest.digest()[:16]

    def encrypt_string(self, data: bytes, objnum: int, gen: int = 0) -> bytes:
        """Encrypt one string's bytes; the result embeds its 16-byte IV first."""
        if self._key is None:
            raise ValueError("file key not recovered yet")
        iv = self._iv_for(objnum, gen)
        padded = pkcs7_pad(data)
        return iv + aes_cbc_encrypt(self.object_key(objnum, gen), iv, padded)

    def decrypt_string(self, data: bytes, objnum: int, gen: int = 0) -> bytes:
        """Decrypt one string's bytes (IV embedded in the first 16 bytes)."""
        if self._key is None:
            raise ValueError("file key not recovered yet")
        if len(data) < 32:
            raise ValueError(
                f"encrypted string in object {objnum} is too short ({len(data)} bytes)"
            )
        iv, body = data[:16], data[16:]
        return pkcs7_unpad(
            aes_cbc_decrypt(self.object_key(objnum, gen), iv, body)
        )

    def encrypt_stream(self, data: bytes, objnum: int, gen: int = 0) -> bytes:
        """Encrypt one stream's data (same shape as :meth:`encrypt_string`)."""
        return self.encrypt_string(data, objnum, gen)

    def decrypt_stream(self, data: bytes, objnum: int, gen: int = 0) -> bytes:
        """Decrypt one stream's data."""
        return self.decrypt_string(data, objnum, gen)

    def _iv_for(self, objnum: int, gen: int) -> bytes:
        """The deterministic IV for an object, or ``os.urandom`` without a seed."""
        if self._seed is None:
            return os.urandom(16)
        return hashlib.sha256(
            self._seed + struct.pack("<I", objnum) + struct.pack("<I", gen) + b"iv"
        ).digest()[:16]

    # -- derived values for the tests --------------------------------------

    @property
    def o(self) -> bytes:
        return self._o

    @property
    def u(self) -> bytes:
        return self._u

    @property
    def permissions(self) -> int:
        return self._p

    @property
    def revision(self) -> int:
        return self._rev

    @property
    def version(self) -> int:
        return self._v

    @property
    def key(self) -> Optional[bytes]:
        return self._key


_ZERO_IV = b"\x00" * 16


def _compute_perms(file_key: bytes, permissions: int, encrypt_metadata: bool) -> bytes:
    """Algorithm 3.10: the 16-byte /Perms value (AES-256, zero IV)."""
    flag = b"T" if encrypt_metadata else b"F"
    block = (
        struct.pack("<I", permissions & 0xFFFFFFFF)
        + b"\xff\xff\xff\xff"
        + flag
        + b"adb"
        + b"\x00\x00\x00\x00"
    )
    return aes_ecb_encrypt(file_key, block)


class _RandomSource:
    """Deterministic stand-in for ``os.urandom`` (production uses no seed)."""

    def __init__(self, seed: Optional[bytes]) -> None:
        self._seed = seed

    def _derive(self, purpose: bytes, length: int) -> bytes:
        if self._seed is None:
            return os.urandom(length)
        return hashlib.sha256(self._seed + purpose).digest()[:length]

    def file_key(self) -> bytes:
        return self._derive(b"file-key", 32)

    def salts(self, purpose: bytes) -> bytes:
        return self._derive(purpose, 8)


# ---------------------------------------------------------------------------
# Document-level parsing (shared by encrypt_pdf / decrypt_pdf)
# ---------------------------------------------------------------------------

_XREF_KEYWORD = b"xref\n"
_SUBSECTION_RE = re.compile(rb"(\d+)\s+(\d+)\s*\n")
_ENTRY_RE = re.compile(rb"(\d{10})\s(\d{5})\s([fn])")
_TRAILER_RE = re.compile(rb"trailer\s*\n(<<.*?>>)\s*\nstartxref", re.S)
_SIZE_RE = re.compile(rb"/Size\s+(\d+)")
_ROOT_RE = re.compile(rb"/Root\s+(\d+)\s+0\s+R")
_INFO_RE = re.compile(rb"/Info\s+(\d+)\s+0\s+R")
_ENCRYPT_RE = re.compile(rb"/Encrypt\s+(\d+)\s+0\s+R")
_ID_RE = re.compile(rb"/ID\s*\[\s*<([0-9a-fA-F]*)>\s*<([0-9a-fA-F]*)>\s*\]")

_NAME_BYTES = frozenset(
    b"!$&'*+,-.0123456789;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ^_`"
    b"abcdefghijklmnopqrstuvwxyz|~"
)


def _parse_xref_offsets(data: bytes) -> Dict[int, int]:
    """Parse the first classic xref section; {obj num: byte offset} for in-use entries."""
    pos = data.index(_XREF_KEYWORD) + len(_XREF_KEYWORD)
    offsets: Dict[int, int] = {}
    while pos < len(data):
        header = _SUBSECTION_RE.match(data, pos)
        if header is None:
            break
        start, count = int(header.group(1)), int(header.group(2))
        pos = header.end()
        for index in range(count):
            entry = _ENTRY_RE.match(data, pos)
            if entry is None:
                raise ValueError(f"malformed xref entry at offset {pos}")
            offset, kind = int(entry.group(1)), entry.group(3)
            if kind == b"n":
                offsets[start + index] = offset
            pos = data.index(b"\n", pos) + 1
    if not offsets:
        raise ValueError("no in-use xref entries found")
    return offsets


def _parse_document(
    data: bytes,
) -> Tuple[bytes, Dict[int, int], Dict[int, bytes], bytes]:
    """Split a rendered PDF into header, offsets, object bodies and trailer bytes."""
    offsets = _parse_xref_offsets(data)
    first_offset = offsets.get(1, min(offsets.values()))
    header = data[:first_offset]
    bodies: Dict[int, bytes] = {}
    for num, offset in offsets.items():
        end = data.index(b"endobj", offset) + len(b"endobj")
        if data[end : end + 1] == b"\n":
            end += 1
        bodies[num] = data[offset:end]
    match = _TRAILER_RE.search(data)
    if match is None:
        raise ValueError("no trailer dictionary found")
    return header, offsets, bodies, match.group(1)


def _parse_trailer(raw: bytes) -> Dict[str, Optional[int]]:
    """Extract /Size /Root /Info /Encrypt and the first /ID element."""
    size = _SIZE_RE.search(raw)
    root = _ROOT_RE.search(raw)
    if size is None or root is None:
        raise ValueError(f"trailer missing /Size or /Root: {raw!r}")
    info = _INFO_RE.search(raw)
    encrypt = _ENCRYPT_RE.search(raw)
    ids = _ID_RE.search(raw)
    if ids is None:
        raise ValueError(f"trailer missing /ID: {raw!r}")
    return {
        "size": int(size.group(1)),
        "root": int(root.group(1)),
        "info": int(info.group(1)) if info is not None else None,
        "encrypt": int(encrypt.group(1)) if encrypt is not None else None,
        "id0": ids.group(1).decode("ascii"),
    }


def _build_trailer(
    size: int,
    root: int,
    info: Optional[int],
    id0: bytes,
    encrypt: Optional[int],
) -> Dict[PdfName, Any]:
    """Rebuild the trailer dictionary in the writer's canonical key order."""
    trailer: Dict[PdfName, Any] = {
        N("Size"): size,
        N("Root"): ObjectId(root),
    }
    if info is not None:
        trailer[N("Info")] = ObjectId(info)
    trailer[N("ID")] = [PdfHexString(id0), PdfHexString(id0)]
    if encrypt is not None:
        trailer[N("Encrypt")] = ObjectId(encrypt)
    return trailer


def _hex_bytes(raw: bytes) -> bytes:
    """Decode a hex string's content (whitespace tolerated, odd length padded)."""
    digits = bytes(byte for byte in raw if byte not in b" \t\r\n\x0b\x0c")
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii"))


# ---------------------------------------------------------------------------
# Body tokenizer: locate strings, hex strings and streams inside a body
# ---------------------------------------------------------------------------

_LITERAL = 1
_HEX = 2
_STREAM = 3


def _scan_literal(body: bytes, start: int) -> int:
    """End offset of the literal string starting at ``start`` (a ``(``)."""
    n = len(body)
    depth = 1
    i = start + 1
    while i < n:
        byte = body[i]
        if byte == 0x5C:  # backslash escape
            if i + 1 >= n:
                return n
            if body[i + 1] == 0x0D:
                i += 3 if i + 2 < n and body[i + 2] == 0x0A else 2
            elif body[i + 1] == 0x0A:
                i += 2
            elif 0x30 <= body[i + 1] <= 0x37:
                j = i + 1
                while j < n and j < i + 4 and 0x30 <= body[j] <= 0x37:
                    j += 1
                i = j
            else:
                i += 2
        elif byte == 0x28:
            depth += 1
            i += 1
        elif byte == 0x29:
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    raise ValueError("unterminated literal string")


def _scan_hex(body: bytes, start: int) -> int:
    """End offset of the hex string starting at ``start`` (a ``<``)."""
    i = body.find(b">", start + 1)
    if i == -1:
        raise ValueError("unterminated hex string")
    return i + 1


def _scan_name(body: bytes, start: int) -> int:
    """End offset of the name starting at ``start`` (a ``/``)."""
    n = len(body)
    i = start + 1
    while i < n:
        byte = body[i]
        if byte in _NAME_BYTES:
            i += 1
        elif byte == 0x23 and i + 2 < n:  # #XX escape
            i += 3
        else:
            break
    return i


def _tokenize(body: bytes) -> List[Tuple[int, int, int]]:
    """Return ``(kind, start, end)`` spans for literal/hex strings and streams."""
    spans: List[Tuple[int, int, int]] = []
    n = len(body)
    i = 0
    while i < n:
        byte = body[i]
        if byte == 0x28:  # (
            start = i
            i = _scan_literal(body, i)
            spans.append((_LITERAL, start, i))
        elif byte == 0x3C:  # < : << is a dict, <..> is a hex string
            if i + 1 < n and body[i + 1] == 0x3C:
                i += 2
            else:
                start = i
                i = _scan_hex(body, i)
                spans.append((_HEX, start, i))
        elif byte == 0x2F:  # /
            i = _scan_name(body, i)
        elif byte == 0x73 and body.startswith(b"stream\n", i):
            data_start = i + len(b"stream\n")
            length = _stream_length(body, i)
            spans.append((_STREAM, data_start, data_start + length))
            tail = body.find(b"endstream", data_start + length)
            i = tail + len(b"endstream") if tail != -1 else data_start + length
        else:
            i += 1
    return spans


def _stream_length(body: bytes, stream_pos: int) -> int:
    """The /Length of the stream whose keyword starts at ``stream_pos``."""
    matches = list(re.finditer(rb"/Length\s+(\d+)", body[:stream_pos]))
    if not matches:
        raise ValueError("stream object without /Length")
    return int(matches[-1].group(1))


def _replace_stream_length(prefix: bytes, new_length: int) -> bytes:
    """Rewrite the stream dict's /Length to ``new_length`` (last match wins)."""
    matches = list(re.finditer(rb"/Length\s+\d+", prefix))
    if not matches:
        raise ValueError("stream object without /Length")
    match = matches[-1]
    return (
        prefix[: match.start()]
        + b"/Length "
        + str(new_length).encode("ascii")
        + prefix[match.end() :]
    )


def _unescape_literal(raw: bytes) -> bytes:
    """Decode a literal string's content bytes (reverse of escape_string)."""
    out = bytearray()
    n = len(raw)
    i = 0
    while i < n:
        byte = raw[i]
        if byte == 0x5C:
            if i + 1 >= n:
                out.append(0x5C)
                i += 1
                continue
            esc = raw[i + 1]
            if esc == 0x6E:
                out.append(0x0A)
                i += 2
            elif esc == 0x72:
                out.append(0x0D)
                i += 2
            elif esc == 0x74:
                out.append(0x09)
                i += 2
            elif esc == 0x62:
                out.append(0x08)
                i += 2
            elif esc == 0x66:
                out.append(0x0C)
                i += 2
            elif esc == 0x0A:
                i += 2
            elif esc == 0x0D:
                i += 3 if i + 2 < n and raw[i + 2] == 0x0A else 2
            elif 0x30 <= esc <= 0x37:
                j = i + 1
                while j < n and j < i + 4 and 0x30 <= raw[j] <= 0x37:
                    j += 1
                out.append(int(raw[i + 1 : j], 8) & 0xFF)
                i = j
            else:
                out.append(esc)
                i += 2
        else:
            out.append(byte)
            i += 1
    return bytes(out)


# ---------------------------------------------------------------------------
# Full-document transforms
# ---------------------------------------------------------------------------


def _encrypt_body(body: bytes, objnum: int, handler: StandardSecurityHandler) -> bytes:
    """Re-encode one object body: strings as encrypted hex, streams encrypted."""
    spans = _tokenize(body)
    out = bytearray()
    pos = 0
    for kind, start, end in spans:
        out += body[pos:start]
        if kind == _LITERAL:
            plain = _unescape_literal(body[start + 1 : end - 1])
            out += encode_hex_string(handler.encrypt_string(plain, objnum))
        elif kind == _HEX:
            plain = _hex_bytes(body[start + 1 : end - 1])
            out += encode_hex_string(handler.encrypt_string(plain, objnum))
        else:  # stream: rewrite /Length and emit the encrypted data
            cipher = handler.encrypt_stream(body[start:end], objnum)
            prefix = _replace_stream_length(bytes(out), len(cipher))
            return prefix + cipher + b"\nendstream\nendobj\n"
        pos = end
    out += body[pos:]
    return bytes(out)


def _decrypt_body(body: bytes, objnum: int, handler: StandardSecurityHandler) -> bytes:
    """Re-encode one object body: encrypted hex/literal strings and streams decoded."""
    spans = _tokenize(body)
    out = bytearray()
    pos = 0
    for kind, start, end in spans:
        out += body[pos:start]
        if kind == _LITERAL:
            cipher = _unescape_literal(body[start + 1 : end - 1])
            plain = handler.decrypt_string(cipher, objnum)
            out += encode_string(plain)
        elif kind == _HEX:
            cipher = _hex_bytes(body[start + 1 : end - 1])
            plain = handler.decrypt_string(cipher, objnum)
            out += encode_string(plain)
        else:  # stream: rewrite /Length and emit the plaintext data
            plain = handler.decrypt_stream(body[start:end], objnum)
            prefix = _replace_stream_length(bytes(out), len(plain))
            return prefix + plain + b"\nendstream\nendobj\n"
        pos = end
    out += body[pos:]
    return bytes(out)


def _parse_encrypt_dict(body: bytes) -> Dict[str, Any]:
    """Extract the /Encrypt dictionary entries from its object body bytes."""
    v_match = re.search(rb"/V\s+(\d+)", body)
    r_match = re.search(rb"/R\s+(\d+)", body)
    length_match = re.search(rb"/Length\s+(\d+)", body)
    p_match = re.search(rb"/P\s+(\d+)", body)
    if v_match is None or r_match is None or length_match is None or p_match is None:
        raise ValueError("encryption dictionary missing /V /R /Length or /P")

    def hex_value(name: bytes) -> bytes:
        match = re.search(rb"/" + name + rb"\s+<([0-9a-fA-F]*)>", body)
        if match is None:
            raise ValueError(f"encryption dictionary missing /{name.decode()}")
        return bytes.fromhex(match.group(1).decode("ascii"))

    cfm_match = re.search(rb"/CFM\s+/([A-Za-z0-9]+)", body)
    v = int(v_match.group(1))
    cfm = cfm_match.group(1).decode("ascii") if cfm_match else ("AESV3" if v >= 5 else "AESV2")
    meta_match = re.search(rb"/EncryptMetadata\s+(true|false)", body)
    entries: Dict[str, Any] = {
        "V": v,
        "R": int(r_match.group(1)),
        "Length": int(length_match.group(1)),
        "P": int(p_match.group(1)),
        "CFM": cfm,
        "EncryptMetadata": meta_match is None or meta_match.group(1) == b"true",
        "O": hex_value(b"O"),
        "U": hex_value(b"U"),
    }
    if v >= 5:
        entries["UE"] = hex_value(b"UE")
        entries["OE"] = hex_value(b"OE")
        entries["Perms"] = hex_value(b"Perms")
    return entries


def encrypt_pdf(data: bytes, spec: EncryptSpec) -> bytes:
    """Encrypt a rendered PDF in place: strings, streams, /Encrypt object + trailer.

    The document's /ID (and hence ``ID0`` for key derivation) is the one
    computed over the unencrypted render, preserved verbatim -- standard
    practice, since the key material depends on it.  The xref section,
    trailer and the /Encrypt object itself are never encrypted.
    """
    header, offsets, bodies, trailer_raw = _parse_document(data)
    trailer = _parse_trailer(trailer_raw)
    if trailer["encrypt"] is not None:
        raise ValueError("document is already encrypted")
    if trailer["info"] is None:
        # Encryption under PDF/A-4 is refused by the builder, so a plain
        # PDF 2.0 document always carries /Info here; refuse defensively.
        raise ValueError("document has no /Info dictionary")
    id0 = _hex_bytes(trailer["id0"].encode("ascii"))
    handler = StandardSecurityHandler.create(spec, id0)
    size = trailer["size"]
    encrypt_num = size

    out = bytearray(header)
    new_offsets = [0] * (size + 1)
    for num in sorted(offsets):
        new_offsets[num] = len(out)
        out += _encrypt_body(bodies[num], num, handler)
    new_offsets[encrypt_num] = len(out)
    out += encode_object(encrypt_num, handler.encrypt_dict())

    xref_section = encode_xref_section(new_offsets, size + 1)
    xref_offset = len(out)
    trailer_dict = _build_trailer(
        size + 1,
        trailer["root"],
        trailer["info"],
        _hex_bytes(trailer["id0"].encode("ascii")),
        encrypt_num,
    )
    out += xref_section
    out += b"trailer\n" + encode_dict(trailer_dict) + b"\n"
    out += b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    return bytes(out)


def decrypt_pdf(data: bytes, password: str) -> bytes:
    """Decrypt an engine-encrypted PDF; returns the exact unencrypted render.

    Recovers the file key from ``password`` (user or owner), decrypts
    every string and stream, drops the /Encrypt object and trailer entry
    and rebuilds the xref, producing bytes identical to the original
    ``DocumentBuilder.render()`` output.  A wrong password raises
    :class:`WrongPasswordError`.
    """
    header, offsets, bodies, trailer_raw = _parse_document(data)
    trailer = _parse_trailer(trailer_raw)
    encrypt_num = trailer["encrypt"]
    if encrypt_num is None:
        raise ValueError("document is not encrypted")
    entries = _parse_encrypt_dict(bodies[encrypt_num])
    handler = StandardSecurityHandler.load(entries, _hex_bytes(trailer["id0"].encode("ascii")))
    handler.recover_key(password)

    size = trailer["size"]
    new_size = size - 1
    out = bytearray(header)
    new_offsets = [0] * new_size
    for num in sorted(num for num in offsets if num != encrypt_num):
        new_offsets[num] = len(out)
        out += _decrypt_body(bodies[num], num, handler)

    xref_section = encode_xref_section(new_offsets, new_size)
    xref_offset = len(out)
    trailer_dict = _build_trailer(
        new_size,
        trailer["root"],
        trailer["info"],
        _hex_bytes(trailer["id0"].encode("ascii")),
        None,
    )
    out += xref_section
    out += b"trailer\n" + encode_dict(trailer_dict) + b"\n"
    out += b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    return bytes(out)

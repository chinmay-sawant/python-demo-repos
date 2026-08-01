"""Pure-Python AES (128/256) and RC4 primitives (stdlib only).

This module owns the symmetric primitives used by the phase 7.5 standard
security handler (engine.encrypt): the AES block cipher (FIPS-197) with
key expansion for 128- and 256-bit keys, CBC mode with PKCS#7 padding
(the PDF AESV2/AESV3 shape: the ciphertext carries its 16-byte IV first,
followed by the padded CBC stream), and the classic RC4 stream cipher
(KSAC/PRGA, as used by the revision-2..4 O/U algorithms).

Everything here is a pure, well-tested reimplementation -- no third-party
modules.  NIST SP 800-38A F.1.1/F.1.5 vectors pin the AES engine and the
RFC 6229-style "Key"/"Plaintext" and "Wiki"/"pedia" pairs pin RC4; the
PDF algorithms themselves are covered by engine/tests/test_encrypt.py.
"""

from __future__ import annotations

from typing import List, Sequence

__all__ = [
    "AES_BLOCK_SIZE",
    "aes_block_decrypt",
    "aes_block_encrypt",
    "aes_cbc_decrypt",
    "aes_cbc_encrypt",
    "aes_ecb_decrypt",
    "aes_ecb_encrypt",
    "pkcs7_pad",
    "pkcs7_unpad",
    "rc4",
]

AES_BLOCK_SIZE = 16

# The AES S-box / inverse S-box, filled at import time from the GF(2^8)
# inverse plus the affine transform (FIPS-197 5.1.1).
_SBOX: List[int] = []
_INV_SBOX: List[int] = []


def _gf_multiply(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8) with reduction polynomial 0x11B."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return result


def _gf_inverse(x: int) -> int:
    """The multiplicative inverse of ``x`` in GF(2^8) (x**254 by Fermat)."""
    if x == 0:
        return 0
    result = 1
    base = x
    exponent = 254
    while exponent:
        if exponent & 1:
            result = _gf_multiply(result, base)
        base = _gf_multiply(base, base)
        exponent >>= 1
    return result


def _build_sboxes() -> None:
    for value in range(256):
        inv = _gf_inverse(value)
        s = inv ^ ((inv << 1) | (inv >> 7)) & 0xFF
        s ^= ((inv << 2) | (inv >> 6)) & 0xFF
        s ^= ((inv << 3) | (inv >> 5)) & 0xFF
        s ^= ((inv << 4) | (inv >> 4)) & 0xFF
        s &= 0xFF
        _SBOX.append(s ^ 0x63)
    for value in range(256):
        _INV_SBOX.append(_SBOX.index(value))


_build_sboxes()

# Round constants for key expansion (FIPS-197 5.2).
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _expand_key(key: bytes) -> List[Tuple[int, int, int, int]]:
    """Key expansion for AES-128 (11 round keys) or AES-256 (15).

    Returns the round keys as a list of ``(w0, w1, w2, w3)`` word rows
    (``Nr + 1`` rows, one word per state column, big-endian bytes).
    """
    if len(key) not in (16, 32):
        raise ValueError(f"AES keys are 16 or 32 bytes, got {len(key)}")
    nk = len(key) // 4
    nr = nk + 6
    words: List[int] = [int.from_bytes(key[i : i + 4], "big") for i in range(0, len(key), 4)]
    for i in range(nk, 4 * (nr + 1)):
        temp = words[i - 1]
        if i % nk == 0:
            temp = ((temp << 8) | (temp >> 24)) & 0xFFFFFFFF  # RotWord
            temp = (
                ((_SBOX[(temp >> 24) & 0xFF] << 24))
                | ((_SBOX[(temp >> 16) & 0xFF] << 16))
                | ((_SBOX[(temp >> 8) & 0xFF] << 8))
                | _SBOX[temp & 0xFF]
            )
            temp ^= _RCON[i // nk - 1] << 24
        elif nk > 6 and i % nk == 4:
            temp = (
                (_SBOX[(temp >> 24) & 0xFF] << 24)
                | (_SBOX[(temp >> 16) & 0xFF] << 16)
                | (_SBOX[(temp >> 8) & 0xFF] << 8)
                | _SBOX[temp & 0xFF]
            )
        words.append(words[i - nk] ^ temp)
    return [
        (words[row * 4], words[row * 4 + 1], words[row * 4 + 2], words[row * 4 + 3])
        for row in range(nr + 1)
    ]


# Precomputed T-tables: SubBytes + ShiftRows + MixColumns folded into four
# 256-entry word tables per direction (the classic rijndael formulation).
# This turns each round into 16 table lookups and 12 XORs, roughly an order
# of magnitude faster than the byte-wise GF arithmetic -- important for the
# revision-6 password hashing, which runs megabytes of AES per document.
_Te0: List[int] = []
_Te1: List[int] = []
_Te2: List[int] = []
_Te3: List[int] = []
_Td0: List[int] = []
_Td1: List[int] = []
_Td2: List[int] = []
_Td3: List[int] = []


def _build_tables() -> None:
    for s in _SBOX:
        m2 = _gf_multiply(s, 2)
        m3 = m2 ^ s
        _Te0.append((m2 << 24) | (s << 16) | (s << 8) | m3)
        _Te1.append((m3 << 24) | (m2 << 16) | (s << 8) | s)
        _Te2.append((s << 24) | (m3 << 16) | (m2 << 8) | s)
        _Te3.append((s << 24) | (s << 16) | (m3 << 8) | m2)
    for inv in _INV_SBOX:
        i14 = _gf_multiply(inv, 14)
        i11 = _gf_multiply(inv, 11)
        i13 = _gf_multiply(inv, 13)
        i9 = _gf_multiply(inv, 9)
        _Td0.append((i14 << 24) | (i9 << 16) | (i13 << 8) | i11)
        _Td1.append((i11 << 24) | (i14 << 16) | (i9 << 8) | i13)
        _Td2.append((i13 << 24) | (i11 << 16) | (i14 << 8) | i9)
        _Td3.append((i9 << 24) | (i13 << 16) | (i11 << 8) | i14)


_build_tables()


def _inv_mix_word(word: int) -> int:
    """InvMixColumns applied to one column word (decryption round keys)."""
    b0 = (word >> 24) & 0xFF
    b1 = (word >> 16) & 0xFF
    b2 = (word >> 8) & 0xFF
    b3 = word & 0xFF
    return (
        ((_gf_multiply(b0, 14) ^ _gf_multiply(b1, 11) ^ _gf_multiply(b2, 13) ^ _gf_multiply(b3, 9)) << 24)
        | ((_gf_multiply(b0, 9) ^ _gf_multiply(b1, 14) ^ _gf_multiply(b2, 11) ^ _gf_multiply(b3, 13)) << 16)
        | ((_gf_multiply(b0, 13) ^ _gf_multiply(b1, 9) ^ _gf_multiply(b2, 14) ^ _gf_multiply(b3, 11)) << 8)
        | (_gf_multiply(b0, 11) ^ _gf_multiply(b1, 13) ^ _gf_multiply(b2, 9) ^ _gf_multiply(b3, 14))
    )


def _encrypt_block(round_keys: List[Tuple[int, int, int, int]], block: bytes) -> bytes:
    # Initial AddRoundKey, then T-table rounds: each round's word columns
    # are Te0/Te1/Te2/Te3 lookups over the previous state's bytes, XORed
    # with the next round key.
    w0, w1, w2, w3 = round_keys[0]
    b0 = int.from_bytes(block[0:4], "big") ^ w0
    b1 = int.from_bytes(block[4:8], "big") ^ w1
    b2 = int.from_bytes(block[8:12], "big") ^ w2
    b3 = int.from_bytes(block[12:16], "big") ^ w3
    w0, w1, w2, w3 = round_keys[1]
    t0 = _Te0[b0 >> 24] ^ _Te1[(b1 >> 16) & 0xFF] ^ _Te2[(b2 >> 8) & 0xFF] ^ _Te3[b3 & 0xFF] ^ w0
    t1 = _Te0[b1 >> 24] ^ _Te1[(b2 >> 16) & 0xFF] ^ _Te2[(b3 >> 8) & 0xFF] ^ _Te3[b0 & 0xFF] ^ w1
    t2 = _Te0[b2 >> 24] ^ _Te1[(b3 >> 16) & 0xFF] ^ _Te2[(b0 >> 8) & 0xFF] ^ _Te3[b1 & 0xFF] ^ w2
    t3 = _Te0[b3 >> 24] ^ _Te1[(b0 >> 16) & 0xFF] ^ _Te2[(b1 >> 8) & 0xFF] ^ _Te3[b2 & 0xFF] ^ w3
    for w0, w1, w2, w3 in round_keys[2:-1]:
        t0, t1, t2, t3 = (
            _Te0[t0 >> 24] ^ _Te1[(t1 >> 16) & 0xFF] ^ _Te2[(t2 >> 8) & 0xFF] ^ _Te3[t3 & 0xFF] ^ w0,
            _Te0[t1 >> 24] ^ _Te1[(t2 >> 16) & 0xFF] ^ _Te2[(t3 >> 8) & 0xFF] ^ _Te3[t0 & 0xFF] ^ w1,
            _Te0[t2 >> 24] ^ _Te1[(t3 >> 16) & 0xFF] ^ _Te2[(t0 >> 8) & 0xFF] ^ _Te3[t1 & 0xFF] ^ w2,
            _Te0[t3 >> 24] ^ _Te1[(t0 >> 16) & 0xFF] ^ _Te2[(t1 >> 8) & 0xFF] ^ _Te3[t2 & 0xFF] ^ w3,
        )
    w0, w1, w2, w3 = round_keys[-1]
    return bytes(
        (
            _SBOX[t0 >> 24] ^ ((w0 >> 24) & 0xFF),
            _SBOX[(t1 >> 16) & 0xFF] ^ ((w0 >> 16) & 0xFF),
            _SBOX[(t2 >> 8) & 0xFF] ^ ((w0 >> 8) & 0xFF),
            _SBOX[t3 & 0xFF] ^ (w0 & 0xFF),
            _SBOX[(t1 >> 24) & 0xFF] ^ ((w1 >> 24) & 0xFF),
            _SBOX[(t2 >> 16) & 0xFF] ^ ((w1 >> 16) & 0xFF),
            _SBOX[(t3 >> 8) & 0xFF] ^ ((w1 >> 8) & 0xFF),
            _SBOX[t0 & 0xFF] ^ (w1 & 0xFF),
            _SBOX[(t2 >> 24) & 0xFF] ^ ((w2 >> 24) & 0xFF),
            _SBOX[(t3 >> 16) & 0xFF] ^ ((w2 >> 16) & 0xFF),
            _SBOX[(t0 >> 8) & 0xFF] ^ ((w2 >> 8) & 0xFF),
            _SBOX[t1 & 0xFF] ^ (w2 & 0xFF),
            _SBOX[(t3 >> 24) & 0xFF] ^ ((w3 >> 24) & 0xFF),
            _SBOX[(t0 >> 16) & 0xFF] ^ ((w3 >> 16) & 0xFF),
            _SBOX[(t1 >> 8) & 0xFF] ^ ((w3 >> 8) & 0xFF),
            _SBOX[t2 & 0xFF] ^ (w3 & 0xFF),
        )
    )


def _decrypt_block(round_keys: List[Tuple[int, int, int, int]], block: bytes) -> bytes:
    # Ciphertext AddRoundKey with the final key, then inverse T-table
    # rounds over the InvMixColumns-transformed middle keys.
    w0, w1, w2, w3 = round_keys[-1]
    b0 = int.from_bytes(block[0:4], "big") ^ w0
    b1 = int.from_bytes(block[4:8], "big") ^ w1
    b2 = int.from_bytes(block[8:12], "big") ^ w2
    b3 = int.from_bytes(block[12:16], "big") ^ w3
    w0, w1, w2, w3 = round_keys[-2]
    t0 = _Td0[b0 >> 24] ^ _Td1[(b3 >> 16) & 0xFF] ^ _Td2[(b2 >> 8) & 0xFF] ^ _Td3[b1 & 0xFF] ^ w0
    t1 = _Td0[b1 >> 24] ^ _Td1[(b0 >> 16) & 0xFF] ^ _Td2[(b3 >> 8) & 0xFF] ^ _Td3[b2 & 0xFF] ^ w1
    t2 = _Td0[b2 >> 24] ^ _Td1[(b1 >> 16) & 0xFF] ^ _Td2[(b0 >> 8) & 0xFF] ^ _Td3[b3 & 0xFF] ^ w2
    t3 = _Td0[b3 >> 24] ^ _Td1[(b2 >> 16) & 0xFF] ^ _Td2[(b1 >> 8) & 0xFF] ^ _Td3[b0 & 0xFF] ^ w3
    for w0, w1, w2, w3 in round_keys[-3:0:-1]:
        t0, t1, t2, t3 = (
            _Td0[t0 >> 24] ^ _Td1[(t3 >> 16) & 0xFF] ^ _Td2[(t2 >> 8) & 0xFF] ^ _Td3[t1 & 0xFF] ^ w0,
            _Td0[t1 >> 24] ^ _Td1[(t0 >> 16) & 0xFF] ^ _Td2[(t3 >> 8) & 0xFF] ^ _Td3[t2 & 0xFF] ^ w1,
            _Td0[t2 >> 24] ^ _Td1[(t1 >> 16) & 0xFF] ^ _Td2[(t0 >> 8) & 0xFF] ^ _Td3[t3 & 0xFF] ^ w2,
            _Td0[t3 >> 24] ^ _Td1[(t2 >> 16) & 0xFF] ^ _Td2[(t1 >> 8) & 0xFF] ^ _Td3[t0 & 0xFF] ^ w3,
        )
    w0, w1, w2, w3 = round_keys[0]
    return bytes(
        (
            _INV_SBOX[t0 >> 24] ^ ((w0 >> 24) & 0xFF),
            _INV_SBOX[(t3 >> 16) & 0xFF] ^ ((w0 >> 16) & 0xFF),
            _INV_SBOX[(t2 >> 8) & 0xFF] ^ ((w0 >> 8) & 0xFF),
            _INV_SBOX[t1 & 0xFF] ^ (w0 & 0xFF),
            _INV_SBOX[(t1 >> 24) & 0xFF] ^ ((w1 >> 24) & 0xFF),
            _INV_SBOX[(t0 >> 16) & 0xFF] ^ ((w1 >> 16) & 0xFF),
            _INV_SBOX[(t3 >> 8) & 0xFF] ^ ((w1 >> 8) & 0xFF),
            _INV_SBOX[t2 & 0xFF] ^ (w1 & 0xFF),
            _INV_SBOX[(t2 >> 24) & 0xFF] ^ ((w2 >> 24) & 0xFF),
            _INV_SBOX[(t1 >> 16) & 0xFF] ^ ((w2 >> 16) & 0xFF),
            _INV_SBOX[(t0 >> 8) & 0xFF] ^ ((w2 >> 8) & 0xFF),
            _INV_SBOX[t3 & 0xFF] ^ (w2 & 0xFF),
            _INV_SBOX[(t3 >> 24) & 0xFF] ^ ((w3 >> 24) & 0xFF),
            _INV_SBOX[(t2 >> 16) & 0xFF] ^ ((w3 >> 16) & 0xFF),
            _INV_SBOX[(t1 >> 8) & 0xFF] ^ ((w3 >> 8) & 0xFF),
            _INV_SBOX[t0 & 0xFF] ^ (w3 & 0xFF),
        )
    )


def _decrypt_round_keys(
    round_keys: List[Tuple[int, int, int, int]]
) -> List[Tuple[int, int, int, int]]:
    """The round keys with InvMixColumns applied to the middle rows."""
    out = [round_keys[0]]
    for row in round_keys[1:-1]:
        out.append(tuple(_inv_mix_word(word) for word in row))
    out.append(round_keys[-1])
    return out


def aes_block_encrypt(key: bytes, block: bytes) -> bytes:
    """Encrypt one 16-byte ``block`` with AES under ``key`` (16 or 32 bytes)."""
    if len(block) != AES_BLOCK_SIZE:
        raise ValueError(f"AES blocks are 16 bytes, got {len(block)}")
    return _encrypt_block(_expand_key(key), block)


def aes_block_decrypt(key: bytes, block: bytes) -> bytes:
    """Decrypt one 16-byte ``block`` with AES under ``key`` (16 or 32 bytes)."""
    if len(block) != AES_BLOCK_SIZE:
        raise ValueError(f"AES blocks are 16 bytes, got {len(block)}")
    return _decrypt_block(_decrypt_round_keys(_expand_key(key)), block)


def _cbc_transform(key: bytes, iv: bytes, data: bytes, decrypt: bool) -> bytes:
    if len(iv) != AES_BLOCK_SIZE:
        raise ValueError(f"AES IVs are 16 bytes, got {len(iv)}")
    if len(data) % AES_BLOCK_SIZE:
        raise ValueError("CBC input must be a multiple of the block size")
    if len(data) == 0:
        return b""
    expand = _expand_key(key)
    if decrypt:
        expand = _decrypt_round_keys(expand)
    output = bytearray()
    previous = iv
    for offset in range(0, len(data), AES_BLOCK_SIZE):
        chunk = data[offset : offset + AES_BLOCK_SIZE]
        if decrypt:
            plain = _decrypt_block(expand, chunk)
            output += bytes(byte ^ prev for byte, prev in zip(plain, previous))
            previous = chunk
        else:
            mixed = bytes(byte ^ prev for byte, prev in zip(chunk, previous))
            cipher = _encrypt_block(expand, mixed)
            output += cipher
            previous = cipher
    return bytes(output)


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Encrypt ``data`` (a multiple of 16 bytes) in AES-CBC mode."""
    return _cbc_transform(key, iv, data, decrypt=False)


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Decrypt ``data`` (a multiple of 16 bytes) in AES-CBC mode."""
    return _cbc_transform(key, iv, data, decrypt=True)


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """Encrypt ``data`` (a multiple of 16 bytes) in AES-ECB mode."""
    if len(data) % AES_BLOCK_SIZE:
        raise ValueError("ECB input must be a multiple of the block size")
    expand = _expand_key(key)
    return b"".join(
        _encrypt_block(expand, data[offset : offset + AES_BLOCK_SIZE])
        for offset in range(0, len(data), AES_BLOCK_SIZE)
    )


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    """Decrypt ``data`` (a multiple of 16 bytes) in AES-ECB mode."""
    if len(data) % AES_BLOCK_SIZE:
        raise ValueError("ECB input must be a multiple of the block size")
    expand = _decrypt_round_keys(_expand_key(key))
    return b"".join(
        _decrypt_block(expand, data[offset : offset + AES_BLOCK_SIZE])
        for offset in range(0, len(data), AES_BLOCK_SIZE)
    )


def pkcs7_pad(data: bytes, block_size: int = AES_BLOCK_SIZE) -> bytes:
    """PKCS#7 padding: append ``n`` bytes of value ``n`` to reach a block boundary."""
    amount = block_size - (len(data) % block_size)
    return data + bytes([amount]) * amount


def pkcs7_unpad(data: bytes, block_size: int = AES_BLOCK_SIZE) -> bytes:
    """Strip PKCS#7 padding; raises ``ValueError`` on malformed padding."""
    if not data or len(data) % block_size:
        raise ValueError("PKCS#7 input must be a non-empty block multiple")
    amount = data[-1]
    if amount < 1 or amount > block_size:
        raise ValueError("PKCS#7 padding byte out of range")
    if data[-amount:] != bytes([amount]) * amount:
        raise ValueError("PKCS#7 padding is malformed")
    return data[:-amount]


def rc4(key: bytes, data: bytes) -> bytes:
    """The RC4 stream cipher (KSA then PRGA over ``data``).

    Keys are 1..256 bytes per the classic specification (an empty key is
    rejected rather than hitting a modulo-by-zero in the KSA).
    """
    if not 1 <= len(key) <= 256:
        raise ValueError(f"RC4 keys are 1..256 bytes, got {len(key)}")
    state = list(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + state[i] + key[i % key_len]) & 0xFF
        state[i], state[j] = state[j], state[i]
    output = bytearray(len(data))
    i = 0
    j = 0
    for index in range(len(data)):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        output[index] = data[index] ^ state[(state[i] + state[j]) & 0xFF]
    return bytes(output)

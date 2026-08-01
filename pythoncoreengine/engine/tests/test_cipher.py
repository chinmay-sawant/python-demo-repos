"""Unit tests for the pure-Python AES/RC4 primitives (engine.cipher).

Pins the engine against published vectors:

* AES-128: FIPS-197 / NIST SP 800-38A -- key 000102...0f encrypts
  001122...ff to 69c4e0d86a7b0430d8cdb78070b4c55a.
* AES-256: same plaintext with key 000102...1f gives
  8ea2b7ca516745bfeafc49904b496089.
* CBC: NIST SP 800-38A F.2.1 (four-block run under 2b7e...4f3c with IV
  000102...0f).
* RC4: the classic "Key"/"Plaintext" -> BBF316E8D940AF0AD3 and
  "Wiki"/"pedia" -> 1021BF0420 pairs.

Plus randomised round-trips for both key sizes, CBC across every input
length 0..48 and the PDF-shaped usage (PKCS#7 pad + CBC).
"""

from __future__ import annotations

import os
import unittest

from engine.cipher import (
    AES_BLOCK_SIZE,
    aes_block_decrypt,
    aes_block_encrypt,
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    pkcs7_pad,
    pkcs7_unpad,
    rc4,
)

KEY_128 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
KEY_256 = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
)
PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")
CIPHER_128 = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
CIPHER_256 = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")


class TestAesVectors(unittest.TestCase):
    def test_aes128_nist_vector(self) -> None:
        self.assertEqual(aes_block_encrypt(KEY_128, PLAIN), CIPHER_128)

    def test_aes256_nist_vector(self) -> None:
        self.assertEqual(aes_block_encrypt(KEY_256, PLAIN), CIPHER_256)

    def test_aes128_decrypts_vector(self) -> None:
        self.assertEqual(aes_block_decrypt(KEY_128, CIPHER_128), PLAIN)

    def test_aes256_decrypts_vector(self) -> None:
        self.assertEqual(aes_block_decrypt(KEY_256, CIPHER_256), PLAIN)


class TestAesRoundTrips(unittest.TestCase):
    def test_block_round_trip_random(self) -> None:
        for _ in range(200):
            block = os.urandom(16)
            self.assertEqual(aes_block_decrypt(KEY_128, aes_block_encrypt(KEY_128, block)), block)
            self.assertEqual(aes_block_decrypt(KEY_256, aes_block_encrypt(KEY_256, block)), block)

    def test_key_sizes_validated(self) -> None:
        with self.assertRaises(ValueError):
            aes_block_encrypt(b"\x00" * 24, PLAIN)
        with self.assertRaises(ValueError):
            aes_block_encrypt(KEY_128, b"\x00" * 15)

    def test_ecb_round_trip(self) -> None:
        data = os.urandom(48)
        self.assertEqual(aes_ecb_decrypt(KEY_256, aes_ecb_encrypt(KEY_256, data)), data)
        with self.assertRaises(ValueError):
            aes_ecb_encrypt(KEY_128, b"\x00" * 17)


class TestAesCbc(unittest.TestCase):
    def test_nist_sp800_38a_f21(self) -> None:
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        data = bytes.fromhex(
            "6bc1bee22e409f96e93d7e117393172a"
            "ae2d8a571e03ac9c9eb76fac45af8e51"
            "30c81c46a35ce411e5fbc1191a0a52ef"
            "f69f2445df4f9b17ad2b417be66c3710"
        )
        ciphertext = bytes.fromhex(
            "7649abac8119b246cee98e9b12e9197d"
            "5086cb9b507219ee95db113a917678b2"
            "73bed6b8e3c1743b7116e69e22229516"
            "3ff1caa1681fac09120eca307586e1a7"
        )
        self.assertEqual(aes_cbc_encrypt(key, iv, data), ciphertext)
        self.assertEqual(aes_cbc_decrypt(key, iv, ciphertext), data)

    def test_cbc_round_trip_all_lengths(self) -> None:
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        for key in (KEY_128, KEY_256):
            for length in range(0, 65, AES_BLOCK_SIZE):
                data = bytes((index * 7 + length) & 0xFF for index in range(length))
                ciphertext = aes_cbc_encrypt(key, iv, data)
                self.assertEqual(len(ciphertext), length)
                self.assertEqual(aes_cbc_decrypt(key, iv, ciphertext), data)

    def test_cbc_chaining_differs_from_ecb(self) -> None:
        # A repeated block must not encrypt to the same bytes in CBC.
        data = b"\x11" * 32
        iv = b"\x00" * 16
        ciphertext = aes_cbc_encrypt(KEY_128, iv, data)
        self.assertNotEqual(ciphertext[:16], ciphertext[16:])

    def test_cbc_rejects_partial_block(self) -> None:
        with self.assertRaises(ValueError):
            aes_cbc_encrypt(KEY_128, b"\x00" * 16, b"\x00" * 17)
        with self.assertRaises(ValueError):
            aes_cbc_decrypt(KEY_128, b"\x00" * 15, b"\x00" * 16)


class TestPkcs7(unittest.TestCase):
    def test_pad_lengths(self) -> None:
        self.assertEqual(pkcs7_pad(b""), b"\x10" * 16)
        self.assertEqual(pkcs7_pad(b"x"), b"x" + b"\x0f" * 15)
        self.assertEqual(pkcs7_pad(b"a" * 16), b"a" * 16 + b"\x10" * 16)
        self.assertEqual(pkcs7_pad(b"a" * 31), b"a" * 31 + b"\x01")

    def test_unpad_round_trip(self) -> None:
        for length in range(0, 48):
            data = os.urandom(length)
            self.assertEqual(pkcs7_unpad(pkcs7_pad(data)), data)

    def test_unpad_rejects_malformed(self) -> None:
        with self.assertRaises(ValueError):
            pkcs7_unpad(b"")
        with self.assertRaises(ValueError):
            pkcs7_unpad(b"\x00" * 16)  # padding byte 0
        with self.assertRaises(ValueError):
            pkcs7_unpad(b"\x00" * 15 + b"\x11")  # out of range
        with self.assertRaises(ValueError):
            pkcs7_unpad(b"\x00" * 15 + b"\x02")  # doesn't match
        with self.assertRaises(ValueError):
            pkcs7_unpad(b"\x00" * 17)  # not a block multiple


class TestRc4(unittest.TestCase):
    def test_rc4_key_plaintext_vector(self) -> None:
        self.assertEqual(rc4(b"Key", b"Plaintext").hex().upper(), "BBF316E8D940AF0AD3")

    def test_rc4_wiki_pedia_vector(self) -> None:
        self.assertEqual(rc4(b"Wiki", b"pedia").hex().upper(), "1021BF0420")

    def test_rc4_round_trip(self) -> None:
        for _ in range(50):
            key = os.urandom(16)
            data = os.urandom(64)
            self.assertEqual(rc4(key, rc4(key, data)), data)

    def test_rc4_empty_key_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rc4(b"", b"abc")
        with self.assertRaises(ValueError):
            rc4(b"\x00" * 257, b"abc")
        self.assertEqual(rc4(b"secret", b""), b"")


class TestAesPdfShape(unittest.TestCase):
    def test_iv_prefixed_cbc_round_trip(self) -> None:
        # The PDF AES shape: 16-byte IV stored ahead of the padded CBC body.
        for _ in range(100):
            data = os.urandom(os.urandom(1)[0])
            iv = os.urandom(AES_BLOCK_SIZE)
            padded = pkcs7_pad(data)
            ciphertext = iv + aes_cbc_encrypt(KEY_256, iv, padded)
            self.assertGreaterEqual(len(ciphertext), 32)
            self.assertEqual(len(ciphertext) % AES_BLOCK_SIZE, 0)
            body = aes_cbc_decrypt(KEY_256, ciphertext[:16], ciphertext[16:])
            self.assertEqual(pkcs7_unpad(body), data)

    def test_zero_iv_two_block(self) -> None:
        data = pkcs7_pad(b"round-trip payload")
        ciphertext = aes_cbc_encrypt(KEY_128, b"\x00" * 16, data)
        self.assertEqual(len(ciphertext), 32)
        self.assertEqual(pkcs7_unpad(aes_cbc_decrypt(KEY_128, b"\x00" * 16, ciphertext)), b"round-trip payload")


if __name__ == "__main__":
    unittest.main()

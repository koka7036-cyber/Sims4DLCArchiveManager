"""
Cryptographic Backend for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 6:
- Ed25519 asymmetric signatures (RFC 8032)
- AES-256-GCM authenticated symmetric encryption
- PBKDF2-HMAC-SHA256 key derivation (600,000 iterations)
Uses `cryptography` when available, with pure-Python fallback for headless environments.
"""

import os
import sys
import hmac
import hashlib
import secrets
from typing import Tuple, Optional

# Check for native cryptography library
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import serialization
    _HAS_NATIVE_CRYPTO = True
except ImportError:
    _HAS_NATIVE_CRYPTO = False


def has_native_cryptography() -> bool:
    """Returns True if native cryptography C library is available."""
    return _HAS_NATIVE_CRYPTO


def derive_key_pbkdf2(
    password: str,
    salt: bytes,
    iterations: int = 600000,
    dklen: int = 32
) -> bytes:
    """
    Derives key using PBKDF2-HMAC-SHA256.
    Mandated by PROJECT_SPEC.md section 6.2: 600,000 iterations.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise ValueError("Salt must be at least 16 bytes.")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen)


# ==============================================================================
# Pure-Python Ed25519 Implementation (RFC 8032)
# ==============================================================================

_P = 2**255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P
_Q = 2**252 + 27742317777372353535851937790883648493
_L = 2**252 + 27742317777372353535851937790883648493
_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = 0


def _recover_x(y: int, sign: int) -> int:
    if y >= _P:
        return 0
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        if sign:
            return 0
        return 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = (x * pow(2, (_P - 1) // 4, _P)) % _P
    if (x * x - x2) % _P != 0:
        return 0
    if (x & 1) != sign:
        x = _P - x
    return x


_BX = _recover_x(_BY, 0)
_B = (_BX, _BY)


def _ed_add(P: Tuple[int, int], Q: Tuple[int, int]) -> Tuple[int, int]:
    x1, y1 = P
    x2, y2 = Q
    denom1 = (1 + _D * x1 * x2 * y1 * y2) % _P
    denom2 = (1 - _D * x1 * x2 * y1 * y2) % _P
    x3 = ((x1 * y2 + x2 * y1) * pow(denom1, _P - 2, _P)) % _P
    y3 = ((y1 * y2 + x1 * x2) * pow(denom2, _P - 2, _P)) % _P
    return (x3, y3)


def _ed_scalarmult(P: Tuple[int, int], e: int) -> Tuple[int, int]:
    if e == 0:
        return (0, 1)
    Q = _ed_scalarmult(P, e // 2)
    Q = _ed_add(Q, Q)
    if e & 1:
        Q = _ed_add(Q, P)
    return Q


def _ed_encode_point(P: Tuple[int, int]) -> bytes:
    x, y = P
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(32))


def _ed_decode_point(s: bytes) -> Optional[Tuple[int, int]]:
    if len(s) != 32:
        return None
    y = sum(s[i] << (i * 8) for i in range(32))
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x == 0 and y != 1 and (x * x - (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)) % _P != 0:
        return None
    return (x, y)


def _pure_ed25519_keygen() -> Tuple[bytes, bytes]:
    seed = secrets.token_bytes(32)
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    A = _ed_scalarmult(_B, a)
    pub = _ed_encode_point(A)
    return seed, pub


def _pure_ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    A = _ed_scalarmult(_B, a)
    pub = _ed_encode_point(A)
    r_bytes = hashlib.sha512(h[32:] + msg).digest()
    r = int.from_bytes(r_bytes, "little") % _L
    R = _ed_scalarmult(_B, r)
    R_bytes = _ed_encode_point(R)
    k_bytes = hashlib.sha512(R_bytes + pub + msg).digest()
    k = int.from_bytes(k_bytes, "little") % _L
    S = (r + k * a) % _L
    return R_bytes + S.to_bytes(32, "little")


def _pure_ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(sig) != 64 or len(pub) != 32:
        return False
    R = _ed_decode_point(sig[:32])
    A = _ed_decode_point(pub)
    if R is None or A is None:
        return False
    S = int.from_bytes(sig[32:], "little")
    if S >= _L:
        return False
    k_bytes = hashlib.sha512(sig[:32] + pub + msg).digest()
    k = int.from_bytes(k_bytes, "little") % _L
    SB = _ed_scalarmult(_B, S)
    R_plus_kA = _ed_add(R, _ed_scalarmult(A, k))
    return _ed_encode_point(SB) == _ed_encode_point(R_plus_kA)


# ==============================================================================
# Pure-Python AES-256-GCM Implementation
# ==============================================================================

# Standard AES S-Box & Rcon
_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]

_RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtimes(a: int) -> int:
    return ((a << 1) ^ 0x1b) & 0xff if (a & 0x80) else (a << 1)


def _aes_key_expansion_256(key: bytes) -> list:
    w = list(key)
    for i in range(8, 60):
        temp = w[(i - 1) * 4: i * 4]
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 8]
        elif i % 8 == 4:
            temp = [_SBOX[b] for b in temp]
        for j in range(4):
            w.append(w[(i - 8) * 4 + j] ^ temp[j])
    return w


def _aes_encrypt_block(block: bytes, round_keys: list) -> bytes:
    state = list(block)
    # AddRoundKey 0
    for i in range(16):
        state[i] ^= round_keys[i]

    # 13 rounds
    for r in range(1, 14):
        # SubBytes
        state = [_SBOX[b] for b in state]
        # ShiftRows
        state = [
            state[0], state[5], state[10], state[15],
            state[4], state[9], state[14], state[3],
            state[8], state[13], state[2], state[7],
            state[12], state[1], state[6], state[11]
        ]
        # MixColumns
        new_state = [0] * 16
        for c in range(4):
            idx = c * 4
            s0, s1, s2, s3 = state[idx:idx + 4]
            new_state[idx] = _xtimes(s0) ^ _xtimes(s1) ^ s1 ^ s2 ^ s3
            new_state[idx + 1] = s0 ^ _xtimes(s1) ^ _xtimes(s2) ^ s2 ^ s3
            new_state[idx + 2] = s0 ^ s1 ^ _xtimes(s2) ^ _xtimes(s3) ^ s3
            new_state[idx + 3] = _xtimes(s0) ^ s0 ^ s1 ^ s2 ^ _xtimes(s3)
        state = new_state
        # AddRoundKey
        rk = round_keys[r * 16:(r + 1) * 16]
        for i in range(16):
            state[i] ^= rk[i]

    # Final round 14
    state = [_SBOX[b] for b in state]
    state = [
        state[0], state[5], state[10], state[15],
        state[4], state[9], state[14], state[3],
        state[8], state[13], state[2], state[7],
        state[12], state[1], state[6], state[11]
    ]
    rk = round_keys[14 * 16:15 * 16]
    for i in range(16):
        state[i] ^= rk[i]
    return bytes(state)


def _ghash_multiply(X: int, Y: int) -> int:
    R = 0xe1000000000000000000000000000000
    Z = 0
    V = Y
    for i in range(128):
        if (X >> (127 - i)) & 1:
            Z ^= V
        if V & 1:
            V = (V >> 1) ^ R
        else:
            V >>= 1
    return Z


def _ghash(H_bytes: bytes, A_bytes: bytes, C_bytes: bytes) -> bytes:
    H = int.from_bytes(H_bytes, "big")
    Y = 0

    def process_blocks(data: bytes):
        nonlocal Y
        padded_len = (len(data) + 15) // 16 * 16
        padded = data + b"\x00" * (padded_len - len(data))
        for i in range(0, len(padded), 16):
            blk = int.from_bytes(padded[i:i + 16], "big")
            Y = _ghash_multiply(Y ^ blk, H)

    process_blocks(A_bytes)
    process_blocks(C_bytes)
    len_block = (len(A_bytes) * 8).to_bytes(8, "big") + (len(C_bytes) * 8).to_bytes(8, "big")
    Y = _ghash_multiply(Y ^ int.from_bytes(len_block, "big"), H)
    return Y.to_bytes(16, "big")


def _pure_aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> Tuple[bytes, bytes]:
    rk = _aes_key_expansion_256(key)
    H = _aes_encrypt_block(b"\x00" * 16, rk)

    if len(iv) == 12:
        J0 = iv + b"\x00\x00\x00\x01"
    else:
        J0 = _ghash(H, b"", iv)

    # Encrypt ciphertext via CTR starting at J0 + 1
    ciphertext = bytearray()
    ctr_num = int.from_bytes(J0[12:], "big")
    prefix = J0[:12]

    for i in range(0, len(plaintext), 16):
        ctr_num = (ctr_num + 1) & 0xffffffff
        ctr_block = prefix + ctr_num.to_bytes(4, "big")
        keystream = _aes_encrypt_block(ctr_block, rk)
        chunk = plaintext[i:i + 16]
        for b1, b2 in zip(chunk, keystream):
            ciphertext.append(b1 ^ b2)

    # Compute auth tag
    S = _ghash(H, aad, bytes(ciphertext))
    tag_keystream = _aes_encrypt_block(J0, rk)
    tag = bytes(b1 ^ b2 for b1, b2 in zip(S, tag_keystream))
    return bytes(ciphertext), tag


def _pure_aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    rk = _aes_key_expansion_256(key)
    H = _aes_encrypt_block(b"\x00" * 16, rk)

    if len(iv) == 12:
        J0 = iv + b"\x00\x00\x00\x01"
    else:
        J0 = _ghash(H, b"", iv)

    # Verify auth tag
    S = _ghash(H, aad, ciphertext)
    tag_keystream = _aes_encrypt_block(J0, rk)
    expected_tag = bytes(b1 ^ b2 for b1, b2 in zip(S, tag_keystream))
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("AES-GCM authentication tag verification failed. Data corrupted or incorrect key.")

    # Decrypt via CTR
    plaintext = bytearray()
    ctr_num = int.from_bytes(J0[12:], "big")
    prefix = J0[:12]

    for i in range(0, len(ciphertext), 16):
        ctr_num = (ctr_num + 1) & 0xffffffff
        ctr_block = prefix + ctr_num.to_bytes(4, "big")
        keystream = _aes_encrypt_block(ctr_block, rk)
        chunk = ciphertext[i:i + 16]
        for b1, b2 in zip(chunk, keystream):
            plaintext.append(b1 ^ b2)

    return bytes(plaintext)


# ==============================================================================
# Unified Public API
# ==============================================================================

def ed25519_generate_keypair() -> Tuple[bytes, bytes]:
    """Generates (private_key_bytes, public_key_bytes). Both 32 bytes."""
    if _HAS_NATIVE_CRYPTO:
        priv = ed25519.Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        return priv_bytes, pub_bytes
    return _pure_ed25519_keygen()


def ed25519_sign(private_key_bytes: bytes, message: bytes) -> bytes:
    """Signs message with Ed25519 private key. Returns 64-byte signature."""
    if _HAS_NATIVE_CRYPTO:
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        return priv.sign(message)
    return _pure_ed25519_sign(private_key_bytes, message)


def ed25519_verify(public_key_bytes: bytes, message: bytes, signature: bytes) -> bool:
    """Verifies Ed25519 signature over message with public key."""
    if _HAS_NATIVE_CRYPTO:
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            pub.verify(signature, message)
            return True
        except Exception:
            return False
    return _pure_ed25519_verify(public_key_bytes, message, signature)


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> Tuple[bytes, bytes]:
    """
    Encrypts plaintext with AES-256-GCM.
    Returns (ciphertext, 16-byte auth tag).
    """
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes for AES-256.")
    if len(iv) != 12:
        raise ValueError("IV must be 12 bytes.")

    if _HAS_NATIVE_CRYPTO:
        aesgcm = AESGCM(key)
        ct_and_tag = aesgcm.encrypt(iv, plaintext, aad)
        return ct_and_tag[:-16], ct_and_tag[-16:]
    return _pure_aes_gcm_encrypt(key, iv, plaintext, aad)


def aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    """
    Decrypts ciphertext and validates auth tag with AES-256-GCM.
    """
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes for AES-256.")
    if len(iv) != 12:
        raise ValueError("IV must be 12 bytes.")
    if len(tag) != 16:
        raise ValueError("Tag must be 16 bytes.")

    if _HAS_NATIVE_CRYPTO:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ciphertext + tag, aad)
    return _pure_aes_gcm_decrypt(key, iv, ciphertext, tag, aad)

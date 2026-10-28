"""
anibase.crypto
~~~~~~~~~~~~~~
Utilitas enkripsi kredensial lokal berbasis Windows Data Protection API (DPAPI).
Mengamankan token OAuth MyAnimeList dan data sensitif lokal tanpa dependensi eksternal.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time

from anibase.logging import app_log

_IS_WINDOWS = sys.platform == "win32"
_DPAPI_INITIALIZED = False
_DPAPI_AVAILABLE = False

if _IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes

        class _DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        _CryptProtectData = ctypes.windll.crypt32.CryptProtectData
        _CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        _CryptProtectData.restype = wintypes.BOOL

        _CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
        _CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        _CryptUnprotectData.restype = wintypes.BOOL

        _LocalFree = ctypes.windll.kernel32.LocalFree

        _DPAPI_INITIALIZED = True
        _DPAPI_AVAILABLE = True
    except Exception as _e:
        _DPAPI_INITIALIZED = True
        _DPAPI_AVAILABLE = False
        app_log(f"Inisialisasi Windows DPAPI gagal: {_e}", level=logging.WARNING)


def is_dpapi_supported() -> bool:
    """Mengecek apakah Windows DPAPI didukung dan tersedia di runtime ini."""
    if os.environ.get("ANIBASE_DISABLE_DPAPI", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return _IS_WINDOWS and _DPAPI_AVAILABLE


def dpapi_encrypt(data: bytes, description: str = "AniBase Credential") -> bytes:
    """
    Mengenkripsi byte menggunakan Windows DPAPI CryptProtectData.
    Hanya pengguna Windows yang sama pada mesin ini yang dapat mendekripsinya.
    """
    if not is_dpapi_supported():
        raise OSError("Windows DPAPI tidak tersedia pada platform ini.")

    data_in = _DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)),
    )
    data_out = _DATA_BLOB()

    # dwFlags = 0 (CRYPTPROTECT_UI_FORBIDDEN jika perlu, default 0)
    if not _CryptProtectData(
        ctypes.byref(data_in),
        description,
        None,
        None,
        None,
        0,
        ctypes.byref(data_out),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _LocalFree(data_out.pbData)


def dpapi_decrypt(ciphertext: bytes) -> bytes:
    """
    Mendekripsi byte menggunakan Windows DPAPI CryptUnprotectData.
    Mengembalikan byte mentah hasil dekripsi.
    """
    if not is_dpapi_supported():
        raise OSError("Windows DPAPI tidak tersedia pada platform ini.")

    data_in = _DATA_BLOB(
        len(ciphertext),
        ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_char)),
    )
    data_out = _DATA_BLOB()

    if not _CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(data_out),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _LocalFree(data_out.pbData)


def _get_vault_key_path() -> str:
    """Mengembalikan path file vault master key terisolasi pengguna."""
    try:
        from anibase.constants import USER_DATA_DIR
        base_dir = USER_DATA_DIR
    except Exception:
        base_dir = os.path.expanduser("~")
    return os.path.join(base_dir, ".vault_key")


def _get_or_create_vault_master_key() -> bytes:
    """
    Memuat atau membuat master key 256-bit untuk enkripsi vault non-Windows.
    File kunci disimpan dengan permission ketat (0600 pada sistem POSIX).
    """
    key_path = _get_vault_key_path()
    if os.path.isfile(key_path):
        try:
            with open(key_path, "rb") as handle:
                existing = handle.read()
            if len(existing) >= 32:
                return existing[:32]
        except OSError:
            pass

    new_key = secrets.token_bytes(32)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(key_path)), exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(key_path, flags, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_key)
        if hasattr(os, "chmod") and sys.platform != "win32":
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
    except OSError as e:
        app_log(f"Gagal menulis vault key file: {e}", level=logging.WARNING)

    return new_key


def _derive_keys(master_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Menurunkan subkunci enkripsi (32 byte) dan autentikasi (32 byte) via PBKDF2-HMAC-SHA256."""
    derived = hashlib.pbkdf2_hmac("sha256", master_key, salt, iterations=10000, dklen=64)
    return derived[:32], derived[32:64]


def _hmac_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Menghasilkan pseudo-random keystream counter-mode menggunakan HMAC-SHA256."""
    blocks = []
    block_count = (length + 31) // 32
    for counter in range(block_count):
        msg = nonce + counter.to_bytes(8, "big")
        blocks.append(hmac.new(key, msg, hashlib.sha256).digest())
    return b"".join(blocks)[:length]


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def portable_encrypt(data: bytes) -> bytes:
    """
    Mengenkripsi byte menggunakan authenticated stream cipher (PBKDF2 + HMAC-SHA256 CTR + Encrypt-then-MAC).
    Output: 16 bytes salt + 16 bytes nonce + 32 bytes HMAC tag + ciphertext.
    """
    master_key = _get_or_create_vault_master_key()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    k_enc, k_auth = _derive_keys(master_key, salt)
    keystream = _hmac_keystream(k_enc, nonce, len(data))
    ciphertext = _xor_bytes(data, keystream)
    tag = hmac.new(k_auth, salt + nonce + ciphertext, hashlib.sha256).digest()
    return salt + nonce + tag + ciphertext


def portable_decrypt(packed: bytes) -> bytes:
    """
    Mendekripsi data terenkripsi portable_v2 dengan verifikasi HMAC tag autentikasi.
    Menolak ciphertext yang dimanipulasi/rusak dengan ValueError.
    """
    if len(packed) < 16 + 16 + 32:
        raise ValueError("Panjang ciphertext portable_v2 tidak mencukupi.")

    salt = packed[:16]
    nonce = packed[16:32]
    expected_tag = packed[32:64]
    ciphertext = packed[64:]

    master_key = _get_or_create_vault_master_key()
    k_enc, k_auth = _derive_keys(master_key, salt)
    actual_tag = hmac.new(k_auth, salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_tag, actual_tag):
        raise ValueError("Integritas ciphertext gagal diverifikasi (HMAC tag mismatch).")

    keystream = _hmac_keystream(k_enc, nonce, len(ciphertext))
    return _xor_bytes(ciphertext, keystream)


def encrypt_auth_payload(payload: dict) -> dict:
    """
    Mengenkripsi dictionary kredensial (misalnya mal_auth) ke dalam format envelope JSON.
    Jika DPAPI tersedia, membungkus ciphertext dengan `_format: dpapi_v1`.
    Jika tidak tersedia (non-Windows / test CI), menggunakan `_format: portable_v2`.
    """
    if not isinstance(payload, dict) or not payload:
        return {}

    raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    if is_dpapi_supported():
        try:
            encrypted_bytes = dpapi_encrypt(raw_bytes, description="AniBase MAL Credential")
            payload_str = base64.b64encode(encrypted_bytes).decode("ascii")
            return {
                "_format": "dpapi_v1",
                "encrypted": True,
                "payload": payload_str,
                "updated_at": int(time.time()),
            }
        except Exception as e:
            app_log(f"Enkripsi DPAPI gagal, beralih ke portable format: {e}", level=logging.WARNING)

    # Portable fallback terenkripsi untuk sistem non-Windows atau test CI
    try:
        encrypted_bytes = portable_encrypt(raw_bytes)
        payload_str = base64.b64encode(encrypted_bytes).decode("ascii")
        return {
            "_format": "portable_v2",
            "encrypted": True,
            "payload": payload_str,
            "updated_at": int(time.time()),
        }
    except Exception as e:
        app_log(f"Enkripsi portable gagal: {e}", level=logging.WARNING)
        # Fallback darurat jika penulisan kunci gagal
        portable_str = base64.b64encode(raw_bytes).decode("ascii")
        return {
            "_format": "portable_v1",
            "encrypted": True,
            "payload": portable_str,
            "updated_at": int(time.time()),
        }


def decrypt_auth_payload(envelope: dict) -> dict:
    """
    Mendekripsi data kredensial dari envelope JSON.
    - Menangani `dpapi_v1` via Windows DPAPI.
    - Menangani `portable_v2` via authenticated keystream cipher.
    - Menangani `portable_v1` (legacy base64).
    - Jika format lama (legacy plaintext tanpa field `encrypted: True`), mengembalikan data apa adanya.
    - Jika dekripsi gagal (rusak/tampered/dipindah ke mesin lain), mengembalikan dict kosong `{}` secara aman.
    """
    if not isinstance(envelope, dict) or not envelope:
        return {}

    # Jika bukan envelope terenkripsi, anggap sebagai format plaintext lama (legacy)
    if envelope.get("encrypted") is not True:
        return envelope

    payload_b64 = envelope.get("payload")
    if not isinstance(payload_b64, str) or not payload_b64:
        return {}

    fmt = envelope.get("_format")

    if fmt == "dpapi_v1":
        try:
            cipher_bytes = base64.b64decode(payload_b64.encode("ascii"))
            decrypted_bytes = dpapi_decrypt(cipher_bytes)
            data = json.loads(decrypted_bytes.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            app_log(f"Dekripsi kredensial DPAPI gagal (file mungkin rusak atau dipindah dari PC lain): {e}", level=logging.WARNING)
            return {}

    if fmt == "portable_v2":
        try:
            cipher_bytes = base64.b64decode(payload_b64.encode("ascii"))
            decrypted_bytes = portable_decrypt(cipher_bytes)
            data = json.loads(decrypted_bytes.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            app_log(f"Dekripsi kredensial portable_v2 gagal: {e}", level=logging.WARNING)
            return {}

    if fmt == "portable_v1":
        try:
            raw_bytes = base64.b64decode(payload_b64.encode("ascii"))
            # Coba portable_decrypt jika sebenarnya format binary
            try:
                decrypted_bytes = portable_decrypt(raw_bytes)
                data = json.loads(decrypted_bytes.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            data = json.loads(raw_bytes.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            app_log(f"Dekripsi kredensial portable_v1 gagal: {e}", level=logging.WARNING)
            return {}

    app_log(f"Format enkripsi tidak dikenal: {fmt}", level=logging.WARNING)
    return {}


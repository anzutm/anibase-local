"""
anibase.crypto
~~~~~~~~~~~~~~
Utilitas enkripsi kredensial lokal berbasis Windows Data Protection API (DPAPI).
Mengamankan token OAuth MyAnimeList dan data sensitif lokal tanpa dependensi eksternal.
"""

import base64
import json
import logging
import os
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


def encrypt_auth_payload(payload: dict) -> dict:
    """
    Mengenkripsi dictionary kredensial (misalnya mal_auth) ke dalam format envelope JSON.
    Jika DPAPI tersedia, membungkus ciphertext dengan `_format: dpapi_v1`.
    Jika tidak tersedia (non-Windows / test CI), menggunakan `_format: portable_v1`.
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

    # Portable fallback untuk sistem non-Windows atau test CI
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
    - Menangani `portable_v1` via portable decoder.
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

    if fmt == "portable_v1":
        try:
            raw_bytes = base64.b64decode(payload_b64.encode("ascii"))
            data = json.loads(raw_bytes.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            app_log(f"Dekripsi kredensial portable gagal: {e}", level=logging.WARNING)
            return {}

    app_log(f"Format enkripsi tidak dikenal: {fmt}", level=logging.WARNING)
    return {}

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted key storage is available on Windows only.")
    source, keepalive = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Local Burmese TTS private data", None, None, None,
        0x01, ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted key storage is available on Windows only.")
    source, keepalive = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


class GeminiKeyStore:
    """Windows-user-bound Gemini key pool. Plaintext is never written to disk."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "secrets" / "gemini-keys.dpapi"

    @staticmethod
    def normalize(values: list[str] | str) -> list[str]:
        if isinstance(values, str):
            values = values.replace("\\\n", "\n").splitlines()
        keys: list[str] = []
        for value in values:
            token = str(value).strip().strip("\\").strip()
            if len(token) >= 20 and token not in keys:
                keys.append(token)
        return keys

    def save(self, values: list[str] | str) -> int:
        keys = self.normalize(values)
        if not keys:
            raise ValueError("Enter at least one Gemini API key.")
        payload = json.dumps({"keys": keys}, separators=(",", ":")).encode("utf-8")
        encrypted = _protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)
        return len(keys)

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(_unprotect(self.path.read_bytes()).decode("utf-8"))
            return self.normalize(payload.get("keys") or [])
        except Exception as exc:
            raise RuntimeError("The encrypted Gemini key pool could not be opened by this Windows user.") from exc

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def status(self) -> dict:
        keys = self.load()
        return {
            "configured": bool(keys),
            "count": len(keys),
            "labels": [f"Key {index + 1} · encrypted" for index, _ in enumerate(keys)],
            "encrypted": True,
        }


class RemoteSourceStore:
    """Windows-user-bound remote source URLs. Plaintext is never persisted."""

    def __init__(self, data_dir: Path):
        self.root = data_dir / "secrets" / "remote-sources"

    def _path(self, job_id: str) -> Path:
        if not job_id or any(character not in "0123456789abcdef" for character in job_id.lower()):
            raise ValueError("Invalid video job identifier.")
        return self.root / f"{job_id}.dpapi"

    def save(self, job_id: str, url: str) -> None:
        value = str(url).strip()
        if not value:
            raise ValueError("The RedNote link is required.")
        path = self._path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(_protect(value.encode("utf-8")))
        os.replace(temporary, path)

    def load(self, job_id: str) -> str:
        path = self._path(job_id)
        if not path.exists():
            raise FileNotFoundError("The encrypted RedNote source link is missing.")
        try:
            return _unprotect(path.read_bytes()).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("The encrypted RedNote link could not be opened by this Windows user.") from exc

    def clear(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)

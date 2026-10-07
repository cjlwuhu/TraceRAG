"""Write-only credentials: Windows DPAPI or a private server AES-GCM key."""

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import stat
import threading
import uuid

from easyrag.console.diagnostics import ConsoleProblem


_LOCK = threading.RLock()
PREFIX = "aesgcm-v1:"


def protect(value, *, decrypt=False):
    """Keep existing current-user DPAPI blobs readable on Windows."""
    if os.name != "nt":
        raise ConsoleProblem("credential_storage", "Windows 密钥不能在当前服务器解密", "请在设置中重新保存密钥。")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ConsoleProblem("credential_storage", "密钥加密或解密失败", "请使用保存密钥的 Windows 用户，或重新输入密钥。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class CredentialStore:
    def __init__(self, state, *, windows=None):
        self.state = Path(state).resolve()
        self.key_path = self.state / "credential-store.key"
        self.windows = os.name == "nt" if windows is None else windows
        self.storage = "windows_dpapi" if self.windows else "linux_aesgcm"

    def _temporary(self, content):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.state / (".credential-" + uuid.uuid4().hex + ".tmp.key")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def _master_key(self, *, create=False):
        with _LOCK:
            try:
                if create and not self.key_path.exists() and not self.key_path.is_symlink():
                    temporary = self._temporary(os.urandom(32))
                    try:
                        # Publish the complete key atomically without replacing a
                        # key installed by a concurrent process. Never rotate on read.
                        try:
                            os.link(temporary, self.key_path)
                        except FileExistsError:
                            pass
                    finally:
                        temporary.unlink(missing_ok=True)
                if self.key_path.is_symlink():
                    raise ValueError("linked key")
                descriptor = os.open(self.key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                                     getattr(os, "O_BINARY", 0))
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("invalid key file")
                    if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                        raise ValueError("private key permissions required")
                    key = os.read(descriptor, 33)
                    if len(key) != 32:
                        raise ValueError("invalid key size")
                    return key
                finally:
                    os.close(descriptor)
            except (OSError, ValueError):
                raise ConsoleProblem("credential_storage", "服务器加密密钥不可读取",
                    "检查服务用户的文件权限；若加密文件丢失，请恢复原文件，或先清除所有已保存密钥再重新输入。") from None

    def seal(self, provider, value, *, allow_create=True):
        if self.windows:
            return base64.b64encode(protect(value.encode())).decode("ascii")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._master_key(create=allow_create)).encrypt(
            nonce, value.encode(), ("tracerag/credential/v1/" + provider).encode())
        return PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")

    def unseal(self, provider, sealed):
        try:
            if self.windows:
                if sealed.startswith(PREFIX):
                    raise ValueError("server credential on Windows")
                return protect(base64.b64decode(sealed, validate=True), decrypt=True).decode()
            if not sealed.startswith(PREFIX):
                raise ValueError("Windows credential on server")
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = base64.b64decode(sealed[len(PREFIX):], validate=True)
            if len(raw) < 28:
                raise ValueError("invalid ciphertext")
            try:
                return AESGCM(self._master_key()).decrypt(
                    raw[:12], raw[12:], ("tracerag/credential/v1/" + provider).encode()).decode()
            except InvalidTag:
                raise ValueError("invalid ciphertext") from None
        except (ValueError, UnicodeError, TypeError, AttributeError):
            raise ConsoleProblem("credential_storage", "无法读取已保存的密钥", "在当前服务的设置中重新保存密钥。", service=provider) from None

    def write_settings(self, path, value):
        content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        temporary = self._temporary(content)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

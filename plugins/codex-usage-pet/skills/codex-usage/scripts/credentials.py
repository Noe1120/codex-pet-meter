"""Local password entry; secrets remain DPAPI encrypted and never on stdout."""
import base64
import ctypes
import json
import os
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise OSError("余额令牌存储需要 Windows DPAPI。")
    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    function = getattr(library, "CryptUnprotectData" if decrypt else "CryptProtectData")
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("Windows 无法处理凭证，请在当前 Windows 用户下重新配置。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_secret(directory, platform_id):
    path = directory / "secrets.dpapi.json"
    if not path.exists():
        raise ValueError("未配置余额查询令牌。")
    try:
        encoded = json.loads(path.read_text(encoding="utf-8"))[platform_id]
        return crypt(base64.b64decode(encoded, validate=True), decrypt=True).decode("utf-8")
    except (KeyError, ValueError, UnicodeError, OSError):
        raise ValueError("余额令牌缺失或无法解密，请重新配置。") from None


def save_secret(directory, platform_id, token):
    if not token or any(ord(c) < 32 or ord(c) > 126 for c in token):
        raise ValueError("令牌应为可见 ASCII 字符，不能包含换行。")
    path = directory / "secrets.dpapi.json"
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    values[platform_id] = base64.b64encode(crypt(token.encode("utf-8"))).decode("ascii")
    atomic_json(path, values)
    # A new credential may identify another account. Do not retain its predecessor's balance.
    for filename in ('balances.local.json', 'daily-spending.local.json'):
        cache_path = directory / filename
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache.pop(platform_id, None)
                atomic_json(cache_path, cache)
            except (ValueError, AttributeError):
                cache_path.unlink(missing_ok=True)


def prompt_secret(directory, platform_id, platform_name):
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.title("Codex 用量宠物 · 本地凭证")
    window.configure(bg="#20242c")
    window.resizable(False, False)
    saved = []
    tk.Label(window, text=f"{platform_name} · 余额查询令牌", bg="#20242c", fg="#f1f3f7",
             font=("Microsoft YaHei UI", 12)).pack(padx=24, pady=(20, 12))
    entry = tk.Entry(window, show="•", width=38, bg="#303641", fg="#f1f3f7", insertbackground="#f1f3f7")
    entry.pack(padx=24)
    tk.Label(window, text="仅在本机加密保存，不发送到对话。", bg="#20242c", fg="#adb6c5").pack(padx=24, pady=12)
    def save():
        try:
            save_secret(directory, platform_id, entry.get().strip())
        except (OSError, ValueError):
            messagebox.showerror("无法保存", "请检查令牌格式和本地存储权限。", parent=window)
            return
        saved.append(True)
        entry.delete(0, "end")
        window.destroy()
    tk.Button(window, text="加密保存", command=save, bg="#303641", fg="#f1f3f7", relief="flat").pack(pady=(0, 20))
    window.attributes("-topmost", True)
    entry.focus_set()
    window.mainloop()
    return bool(saved)

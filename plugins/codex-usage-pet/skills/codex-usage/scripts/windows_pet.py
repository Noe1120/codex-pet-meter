"""Read Codex overlay HWND and its persisted pet anchor. No app injection."""
import ctypes
import json
import time
import threading
from ctypes import wintypes
from pathlib import Path

from bubble_model import PetClickGate, choose_overlay, saved_anchor


class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]


def rect_tuple(rect):
    return rect.left, rect.top, rect.right, rect.bottom


class Desktop:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsIconic': ([wintypes.HWND], wintypes.BOOL),
            'GetWindowLongW': ([wintypes.HWND, ctypes.c_int], ctypes.c_long),
            'SetWindowLongW': ([wintypes.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long),
            'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'MonitorFromWindow': ([wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            'GetMonitorInfoW': ([wintypes.HANDLE, ctypes.POINTER(MonitorInfo)], wintypes.BOOL),
            'SetWindowPos': ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            'GetAncestor': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            'WindowFromPoint': ([wintypes.POINT], wintypes.HWND),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def windows(self):
        rows = []
        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = self.kernel.OpenProcess(0x1000, False, pid.value)
            if not handle:
                return True
            try:
                path = ctypes.create_unicode_buffer(2048)
                length = wintypes.DWORD(2048)
                if not self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                    return True
            finally:
                self.kernel.CloseHandle(handle)
            if 'codex' not in path.value.lower():
                return True
            cls = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            self.user.GetWindowRect(hwnd, ctypes.byref(rect))
            rows.append({'hwnd': int(hwnd), 'pid': pid.value, 'path': path.value,
                         'class': cls.value, 'rect': rect_tuple(rect),
                         'visible': not bool(self.user.IsIconic(hwnd)),
                         'style': self.user.GetWindowLongW(hwnd, -16) & 0xffffffff,
                         'exstyle': self.user.GetWindowLongW(hwnd, -20) & 0xffffffff})
            return True
        self.user.EnumWindows(self.callback_type(visit), 0)
        return rows

    def monitor(self, hwnd):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(hwnd, 2)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError('无法读取屏幕工作区。')
        return rect_tuple(info.monitor), rect_tuple(info.work)

    def locate(self, codex_home: Path):
        windows = self.windows()
        overlay = choose_overlay(windows)
        if overlay is None:
            return {'anchor': None, 'app_present': bool(windows), 'reason': '未找到唯一的可见 Codex 宠物透明窗口'}
        try:
            state = json.loads((codex_home / '.codex-global-state.json').read_text(encoding='utf-8'))
            if state.get('electron-avatar-overlay-open') is not True:
                return {'anchor': None, 'app_present': True, 'reason': '原生宠物未开启'}
            monitor, work = self.monitor(overlay['hwnd'])
            anchor = saved_anchor(state.get('electron-avatar-overlay-bounds', {}), monitor)
            if anchor is None:
                return {'anchor': None, 'app_present': True, 'reason': '宠物位置记录不可用'}
            x, y, _, _ = anchor
            left, top, right, bottom = overlay['rect']
            if not left <= x < right or not top <= y < bottom:
                return {'anchor': None, 'app_present': True, 'reason': '宠物记录与透明窗口位置不一致'}
            return {'anchor': anchor, 'work_area': work, 'hwnd': overlay['hwnd'],
                    'app_present': True, 'reason': '跟随 Codex 保存的宠物位置（拖动结束后更新）'}
        except (OSError, ValueError, TypeError, AttributeError):
            return {'anchor': None, 'app_present': True, 'reason': '暂时无法读取宠物位置记录'}

    def position(self, hwnd, x, y, width, height):
        # SWP_NOACTIVATE: the bubble never steals keyboard focus when following.
        self.user.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, width, height, 0x10 | 0x40)

    def bubble_style(self, hwnd):
        style = self.user.GetWindowLongW(hwnd, -20)
        self.user.SetWindowLongW(hwnd, -20, (style | 0x80 | 0x08000000) & ~0x40000)


class MouseHookData(ctypes.Structure):
    _fields_ = [('point', wintypes.POINT), ('mouse_data', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra', ctypes.c_size_t)]


class PetClickMonitor:
    """Pass-through mouse hook, keeping only the current pet click state in memory."""
    def __init__(self, desktop):
        self.desktop = desktop
        self.gate = PetClickGate()
        self.anchor, self.hwnd = None, None
        user = desktop.user
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.callback = callback_type(self._observe)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.UnhookWindowsHookEx.restype = wintypes.BOOL
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user.GetMessageW.restype = wintypes.BOOL
        user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.PostThreadMessageW.restype = wintypes.BOOL
        desktop.kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        desktop.kernel.GetModuleHandleW.restype = wintypes.HMODULE
        desktop.kernel.GetCurrentThreadId.restype = wintypes.DWORD
        self.hook, self.thread_id = None, None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        if not self.ready.wait(3) or not self.hook:
            raise OSError('无法识别宠物点击，请检查桌面权限。')

    def _listen(self):
        user = self.desktop.user
        self.thread_id = self.desktop.kernel.GetCurrentThreadId()
        self.hook = user.SetWindowsHookExW(14, self.callback, self.desktop.kernel.GetModuleHandleW(None), 0)
        self.ready.set()
        if not self.hook:
            return
        try:
            message = wintypes.MSG()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            user.UnhookWindowsHookEx(self.hook)
            self.hook = None

    def update(self, location):
        anchor, hwnd = location.get('anchor'), location.get('hwnd')
        if not anchor or hwnd != self.hwnd:
            self.gate.cancel()
        self.anchor, self.hwnd = anchor, hwnd

    def _observe(self, code, message, address):
        # Never consume, record or replay mouse input. Only the pet rectangle matters.
        try:
            event = {0x201: 'down', 0x202: 'up', 0x200: 'move'}.get(message)
            if code >= 0 and event and self.anchor:
                point = ctypes.cast(address, ctypes.POINTER(MouseHookData)).contents.point
                x, y, width, height = self.anchor
                hit = x <= point.x < x + width and y <= point.y < y + height
                if event == 'down' and hit:
                    target = self.desktop.user.WindowFromPoint(point)
                    hit = self.desktop.user.GetAncestor(target, 2) == self.hwnd
                self.gate.observe(event, (point.x, point.y), hit, time.monotonic())
        except (OSError, ValueError, TypeError):
            self.gate.cancel()
        return self.desktop.user.CallNextHookEx(None, code, message, address)

    def close(self):
        if self.thread_id and self.thread.is_alive():
            self.desktop.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
            self.thread.join(1)


def enable_dpi_awareness():
    user = ctypes.WinDLL('user32', use_last_error=True)
    try:
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user.SetProcessDPIAware()

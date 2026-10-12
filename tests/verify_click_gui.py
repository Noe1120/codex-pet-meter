"""Exercise real mouse hooks on our own mock pet, never clicking native Codex."""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-usage-pet/skills/codex-usage/scripts'))
from credentials import atomic_json
from desktop_bubble import Bubble
from windows_pet import enable_dpi_awareness


def run():
    enable_dpi_awareness()
    config = ROOT / '.research/click-gui-test/config.local.json'
    atomic_json(config, {'bubble': {'display': 'click'}})
    args = SimpleNamespace(config=config, codex_home=config.parent, demo=True, manual=False, duration=0)
    bubble = Bubble(args, 'gui-click-test')
    target = bubble.tk.Toplevel(bubble.root)
    target.overrideredirect(True)
    target.geometry('112x121+350+500')
    target.attributes('-topmost', True)
    bubble.tk.Label(target, text='模拟角色\n点击测试', bg='#bcd6ff').pack(fill='both', expand=True)
    target.update_idletasks()
    user = bubble.desktop.user
    target_hwnd = user.GetAncestor(target.winfo_id(), 2)
    bubble.desktop.bubble_style(target_hwnd)
    bubble.desktop.locate = lambda _: {
        'anchor': (350, 500, 112, 121), 'hwnd': target_hwnd,
        'work_area': (0, 0, 1920, 1080), 'app_present': True, 'reason': '模拟角色'}
    user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    original = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(original))
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)
        print(('PASS: ' if condition else 'FAIL: ') + message, flush=True)

    def click():
        user.SetCursorPos(406, 560)
        user.mouse_event(2, 0, 0, 0, 0)
        user.mouse_event(4, 0, 0, 0, 0)

    def first_click():
        check(not bubble.visible, 'initially hidden while process stays ready')
        click()

    def drag():
        user.SetCursorPos(406, 560)
        user.mouse_event(2, 0, 0, 0, 0)
        user.mouse_event(1, 30, 0, 0, 0)
        user.mouse_event(1, ctypes.c_uint32(-30).value, 0, 0, 0)
        user.mouse_event(4, 0, 0, 0, 0)

    bubble.root.after(800, first_click)
    bubble.root.after(1300, lambda: check(bubble.visible and bool(user.IsWindowVisible(bubble.hwnd)), 'actual hook shows Tk bubble on click'))
    bubble.root.after(3000, click)
    bubble.root.after(6200, lambda: check(bubble.visible, 'second click resets the five-second timer'))
    bubble.root.after(8350, lambda: check(not bubble.visible and not user.IsWindowVisible(bubble.hwnd), 'hidden five seconds after last click'))
    bubble.root.after(8600, drag)
    bubble.root.after(9150, lambda: check(not bubble.visible, 'drag excursion does not trigger'))
    bubble.root.after(9350, click)
    bubble.root.after(9750, lambda: check(bubble.visible, 'can show again after hiding'))
    bubble.root.after(9900, bubble.close)
    try:
        bubble.root.mainloop()
    finally:
        user.mouse_event(4, 0, 0, 0, 0)
        bubble.click_monitor.close()
        user.SetCursorPos(original.x, original.y)
    assert not failures, failures
    print('GUI click/timer test PASS; no native pet or account data used.', flush=True)


if __name__ == '__main__':
    run()

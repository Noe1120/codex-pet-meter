"""Start only the plugin's demo bubble, inspect it, capture it, then stop it."""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-usage-pet/skills/codex-usage/scripts'))
from bubble_control import start, stop, status
from credentials import atomic_json
from windows_pet import Desktop, enable_dpi_awareness
from bubble_model import bubble_position


def run():
    from PIL import ImageGrab, Image, ImageDraw
    enable_dpi_awareness()
    folder = ROOT / '.research/bubble-gui-test'
    config = folder / 'config.local.json'
    atomic_json(config, {'platforms': [], 'providers': {}, 'bubble': {'manual': True, 'position': [350, 350], 'display': 'always', 'cost_source': 'platform'}})
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    try:
        result = start(config, home, demo=True, manual=True)
        assert result['running'], result
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(0.2)
            details = status(folder)
            if details.get('visible'):
                break
        assert details.get('visible'), details
        duplicate = start(config, home, demo=True, manual=True)
        assert duplicate['pid'] == details['pid']
        desktop = Desktop()
        hwnd = details['hwnd']
        assert desktop.user.IsWindowVisible(hwnd)
        extended = desktop.user.GetWindowLongW(hwnd, -20) & 0xffffffff
        assert extended & 0x08000080 == 0x08000080, hex(extended)
        rect = wintypes.RECT()
        desktop.user.GetWindowRect(hwnd, ctypes.byref(rect))
        assert (rect.right - rect.left, rect.bottom - rect.top) == (224, 150)
        time.sleep(1)
        image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True).convert('RGBA')
        # Only our oval and thought dots remain; no desktop background in the saved preview.
        mask = Image.new('L', image.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((5, 5, 219, 123), fill=255)
        draw.ellipse((75, 122, 93, 133), fill=255)
        draw.ellipse((96, 141, 103, 147), fill=255)
        image.putalpha(mask)
        image = Image.alpha_composite(Image.new('RGBA', image.size, (255, 255, 255, 0)), image)
        target = ROOT / '.research/bubble-demo.png'
        image.save(target)
        assert any(r < 100 and g < 100 and b > 60 and a for r, g, b, a in image.getdata()), 'bubble colors missing'
        print(json.dumps({'gui': 'PASS', 'single_instance': 'PASS', 'noactivate_toolwindow': 'PASS', 'snapshot': str(target)}, ensure_ascii=True))
        location = desktop.locate(home)
        print(json.dumps({'native_location': location}, ensure_ascii=True))
        assert location.get('anchor'), 'Native attachment could not be verified'
        stop(folder)
        fake_home = folder / 'codex-home'
        fake_home.mkdir(exist_ok=True)
        x, y, _, _ = location['anchor']
        monitor, _ = desktop.monitor(location['hwnd'])
        state = {'electron-avatar-overlay-open': True, 'electron-avatar-overlay-bounds': {
            'x': x, 'y': y, 'displayBounds': {'x': monitor[0], 'y': monitor[1], 'width': monitor[2]-monitor[0], 'height': monitor[3]-monitor[1]}}}
        atomic_json(fake_home / '.codex-global-state.json', state)
        atomic_json(config, {'platforms': [], 'providers': {}, 'bubble': {'manual': False, 'display': 'always'}})
        result = start(config, fake_home, demo=True)
        assert result['running'], result
        time.sleep(1.5)
        details = status(folder)
        assert details['mode'] == 'follow', details
        expected = list(bubble_position(location['anchor'], location['work_area'], (224, 150)))
        assert details['visible'] and details['position'] == expected, details
        state['electron-avatar-overlay-bounds']['x'] += 30
        atomic_json(fake_home / '.codex-global-state.json', state)
        time.sleep(1.5)
        moved = status(folder)
        assert moved['position'][0] == expected[0] + 30, moved
        state['electron-avatar-overlay-open'] = False
        atomic_json(fake_home / '.codex-global-state.json', state)
        time.sleep(1.5)
        assert status(folder)['visible'] is False
        print('PASS: follows saved anchor changes; hides when pet is closed; test changes only synthetic state under 2.0.')
    finally:
        result = stop(folder)
        print(json.dumps({'cleanup': result}, ensure_ascii=True))
        assert not result['running']


if __name__ == '__main__':
    run()

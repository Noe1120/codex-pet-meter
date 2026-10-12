"""Own only this plugin's bubble process through a lock and instance token."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from credentials import atomic_json


class InstanceLock:
    def __init__(self, path):
        self.path, self.file = path, None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            return False
        self.file = stream
        return True

    def release(self):
        if self.file:
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


def launch_command(python, script, config, codex_home, demo=False, manual=False):
    python = python.with_name('pythonw.exe') if python.name.lower() == 'python.exe' else python
    command = [str(python), str(script), '--config', str(config), '--codex-home', str(codex_home)]
    if demo:
        command.append('--demo')
    if manual:
        command.append('--manual')
    return command


def should_stop(path, instance):
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('instance') == instance
    except (OSError, ValueError, AttributeError):
        return False


def status(directory):
    probe = InstanceLock(directory / 'bubble.instance.lock')
    running = not probe.acquire()
    probe.release()
    try:
        details = json.loads((directory / 'bubble.status.json').read_text(encoding='utf-8'))
        if not isinstance(details, dict):
            details = {}
    except (OSError, ValueError):
        details = {}
    return {**details, 'running': running}


def start(config, codex_home, demo=False, manual=False):
    current = status(config.parent)
    if current['running']:
        return {**current, 'message': '气泡已在运行，不重复启动。'}
    python = Path(sys.executable)
    pythonw = python.with_name('pythonw.exe')
    if not pythonw.is_file():
        raise ValueError('需要带 Tkinter 的 Windows Python 及 pythonw.exe。')
    (config.parent / 'bubble.status.json').unlink(missing_ok=True)
    command = launch_command(python, Path(__file__).with_name('desktop_bubble.py'), config.resolve(), codex_home.resolve(), demo, manual)
    subprocess.Popen(command, cwd=Path(__file__).parent, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = status(config.parent)
        if current['running'] and current.get('instance'):
            return {**current, 'message': '气泡已启动。'}
        if current.get('state') == 'failed':
            return {**current, 'message': '气泡启动失败，请检查 Tkinter 和桌面权限。'}
        time.sleep(0.1)
    return {'running': False, 'message': '尚未确认气泡启动，请查询 bubble-status。'}


def stop(directory):
    current = status(directory)
    if not current['running']:
        return {'running': False, 'message': '气泡未运行。'}
    if not current.get('instance'):
        return {'running': True, 'message': '气泡正在初始化，请稍后再关闭。'}
    atomic_json(directory / 'bubble.stop.json', {'instance': current['instance']})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not status(directory)['running']:
            return {'running': False, 'message': '气泡已关闭。'}
        time.sleep(0.1)
    return {'running': True, 'message': '已请求关闭，正在等待当前操作结束。'}

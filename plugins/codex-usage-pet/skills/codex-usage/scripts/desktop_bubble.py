"""Transparent companion bubble, controlled by the Codex skill on Windows."""
import argparse
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from balance import query_all
from bubble_control import InstanceLock, should_stop
from bubble_model import balance_time, bubble_position, bubble_summary
from collector import collect, parse_offset
from credentials import atomic_json
from main import DEFAULT_CONFIG, PLUGIN_ROOT, demo_data, load_config, report_lock
from pricing import add_prices
from spending import query_all_spending
from windows_pet import Desktop, PetClickMonitor, enable_dpi_awareness


class RefreshWorker:
    """One background operation at a time; Tk only consumes results."""
    def __init__(self, fetch):
        self.fetch = fetch
        self.results = queue.Queue()
        self.thread = None

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return False
        def work():
            try:
                self.results.put(('data', self.fetch()))
            except Exception:
                self.results.put(('error', '查询暂未完成，保留上次数据。'))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items


class Bubble:
    def __init__(self, args, instance):
        import tkinter as tk
        self.tk, self.args, self.instance = tk, args, instance
        self.directory = args.config.parent
        self.desktop = Desktop()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('Codex 用量宠物 · 余额气泡')
        self.root.overrideredirect(True)
        self.root.configure(bg='#ff00ff')
        self.root.attributes('-transparentcolor', '#ff00ff')
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.98)
        self.width, self.height = 224, 150
        self.root.geometry(f'{self.width}x{self.height}+0+0')
        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                bg='#ff00ff', highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        self.root.update_idletasks()
        self.hwnd = self.desktop.user.GetAncestor(self.root.winfo_id(), 2)
        self.desktop.bubble_style(self.hwnd)
        self.config = load_config(args.config)
        self.settings = self.config.get('bubble', {})
        self.manual = args.manual or bool(self.settings.get('manual', False))
        self.position = self.settings.get('position')
        self.selected = self.settings.get('platform_id')
        self.click_monitor = PetClickMonitor(self.desktop)
        self.data = {'today': '', 'records': [], 'balances': [],
                     'cost_source': self.settings.get('cost_source', 'estimate')}
        self.location = {'anchor': None, 'app_present': True, 'reason': '正在定位宠物'}
        self.error = ''
        self.last_refresh = 0
        self.last_status = 0
        self.last_location = 0
        self.started = time.monotonic()
        self.missing_since = None
        self.drag_origin = None
        self.visible = False
        self.worker = RefreshWorker(self.fetch)
        self.canvas.bind('<ButtonPress-1>', self.drag_begin)
        self.canvas.bind('<B1-Motion>', self.drag_move)
        self.canvas.bind('<ButtonRelease-1>', self.drag_end)
        self.canvas.bind('<Button-3>', self.show_menu)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.report_callback_exception = self.callback_error
        self.publish('starting')
        self.draw()
        self.root.after(50, self.tick)

    def fetch(self):
        config = load_config(self.args.config)
        if self.args.demo:
            data = demo_data()
            data['cost_source'] = config.get('bubble', {}).get('cost_source', 'estimate')
            if data['cost_source'] == 'platform':
                data['daily_spending'] = [{'id': 'demo', 'day': data['today'], 'state': 'success',
                                           'amount': '1.25', 'unit': 'USD',
                                           'success_at': data['generated_at']}]
            return data, config
        with report_lock(self.directory):
            data = add_prices(collect(self.args.codex_home, offset=config.get('timezone_offset', '+08:00')), config)
            data['balances'] = query_all(config.get('platforms', []), self.directory)
            data['cost_source'] = config.get('bubble', {}).get('cost_source', 'estimate')
            if data['cost_source'] == 'platform':
                data['daily_spending'] = query_all_spending(config.get('platforms', []), self.directory,
                                                          offset=config.get('timezone_offset', '+08:00'))
        return data, config

    def apply_config(self, config):
        settings = config.get('bubble', {})
        # Apply external changes once; unchanged on-disk positions must not undo dragging.
        for key, attribute in (('platform_id', 'selected'), ('manual', 'manual'), ('position', 'position')):
            if settings.get(key) != self.settings.get(key):
                setattr(self, attribute, settings.get(key, False if key == 'manual' else None))
        self.config, self.settings = config, dict(settings)

    def callback_error(self, *_):
        self.error = '界面更新异常，请关闭后重新开启气泡。'
        self.publish('failed')
        self.click_monitor.close()
        self.root.destroy()

    def draw(self):
        canvas = self.canvas
        canvas.delete('all')
        outline, white, blue, subtle = '#273772', '#ffffff', '#4b67b5', '#8d9ecb'
        canvas.create_oval(4, 4, 220, 124, fill=white, outline=outline, width=4)
        canvas.create_oval(74, 121, 94, 134, fill=white, outline=outline, width=3)
        canvas.create_oval(95, 140, 104, 148, fill=white, outline=outline, width=2)
        today = datetime.now(parse_offset(self.config.get('timezone_offset', '+08:00'))).date().isoformat()
        summary = bubble_summary(self.data, self.selected, today=today)
        name = summary['name']
        if len(name) > 12:
            name = name[:11] + '…'
        canvas.create_text(112, 25, text=name + ' 余额', fill=blue, font=('Microsoft YaHei UI', 11, 'bold'))
        canvas.create_text(112, 55, text=summary['balance'], fill=outline, font=('Microsoft YaHei UI', 23, 'bold'))
        canvas.create_text(112, 80, text=summary['cost_title'] + ' ' + summary['cost_label'], fill=subtle, font=('Microsoft YaHei UI', 9))
        canvas.create_text(112, 98, text='今日 Token ' + summary['tokens'], fill=blue, font=('Microsoft YaHei UI', 8))
        success = balance_time(summary['success_at'], self.config.get('timezone_offset', '+08:00'))
        stamp = ('模拟数据' if self.args.demo else '查询异常' if self.error else summary['status']
                 or ('今日数据待更新' if not summary['current'] else '更新 ' + success if success else '等待查询'))
        canvas.create_text(112, 114, text=stamp, fill='#a73c59' if summary['stale'] or self.error or summary['status'] == '查询异常' else blue, font=('Microsoft YaHei UI', 8))

    def publish(self, state='running'):
        atomic_json(self.directory / 'bubble.status.json', {
            'instance': self.instance, 'pid': os.getpid(), 'state': state, 'hwnd': int(self.hwnd),
            'heartbeat': time.time(), 'visible': self.visible, 'mode': 'manual' if self.manual else 'follow',
            'display': self.config.get('bubble', {}).get('display', 'click'),
            'platform_id': self.selected, 'demo': self.args.demo,
            'position': self.position, 'location': self.location.get('reason', ''),
            'error': self.error})

    def tick(self):
        now = time.monotonic()
        if should_stop(self.directory / 'bubble.stop.json', self.instance) or (self.args.duration and now - self.started >= self.args.duration):
            self.close()
            return
        if now - self.last_location >= 0.5:
            self.location = self.desktop.locate(self.args.codex_home)
            self.click_monitor.update(self.location)
            self.last_location = now
        if not self.location['app_present'] and not self.args.demo:
            self.missing_since = self.missing_since or now
            if now - self.missing_since > 15:
                self.close()
                return
        else:
            self.missing_since = None
        anchor = self.location.get('anchor')
        display = self.config.get('bubble', {}).get('display', 'click')
        reveal = display == 'always' or self.click_monitor.gate.visible(now)
        was_visible = self.visible
        if reveal and self.manual:
            if not self.position:
                _, work = self.desktop.monitor(self.hwnd)
                self.position = [work[2] - self.width - 36, work[3] - self.height - 180]
            self.show_at(*self.position)
        elif reveal and anchor:
            self.position = list(bubble_position(anchor, self.location['work_area'], (self.width, self.height)))
            self.show_at(*self.position)
        elif self.visible:
            self.hide()
        for kind, payload in self.worker.drain():
            if kind == 'data':
                self.data, config = payload
                # A menu change may have been saved while the worker was running.
                try:
                    config = load_config(self.args.config)
                except (OSError, ValueError):
                    pass
                self.apply_config(config)
                choices = [b['id'] for b in self.data['balances']]
                if self.selected not in choices:
                    self.selected = choices[0] if choices else None
                self.error = ''
                self.draw()
            else:
                self.error = payload
                self.draw()
        interval = self.config.get('bubble', {}).get('refresh_seconds', 60)
        interval = max(60, min(3600, interval)) if isinstance(interval, int) and not isinstance(interval, bool) else 60
        # Prefetch once while waiting for the first click; subsequent clicks use cached data
        # and can start a refresh, respecting the platform's minimum request interval.
        first_load = self.last_refresh == 0 and bool(anchor)
        if (self.visible or first_load) and now - self.last_refresh >= interval:
            if self.worker.start():
                self.last_refresh = now
        if self.visible != was_visible or now - self.last_status >= 1:
            self.draw()
            self.publish()
            self.last_status = now
        self.root.after(100, self.tick)

    def hide(self):
        self.root.withdraw()
        self.visible = False

    def show_at(self, x, y):
        self.desktop.position(self.hwnd, int(x), int(y), self.width, self.height)
        if not self.visible:
            self.root.deiconify()
            self.desktop.bubble_style(self.hwnd)
            self.visible = True

    def drag_begin(self, event):
        self.drag_origin = (event.x_root, event.y_root, *(self.position or [0, 0]))

    def drag_move(self, event):
        if self.drag_origin:
            mx, my, x, y = self.drag_origin
            if abs(event.x_root - mx) + abs(event.y_root - my) > 3:
                self.manual = True
                self.position = [x + event.x_root - mx, y + event.y_root - my]
                self.show_at(*self.position)

    def drag_end(self, _):
        if self.drag_origin and self.manual:
            self.save_preferences()
        self.drag_origin = None

    def save_preferences(self):
        if self.args.demo:
            return
        try:
            with report_lock(self.directory):
                config = load_config(self.args.config)
                config.setdefault('bubble', {}).update({'manual': self.manual, 'position': self.position, 'platform_id': self.selected})
                atomic_json(self.args.config, config)
                self.settings = dict(config['bubble'])
        except (OSError, ValueError):
            self.error = '设置未能保存，请等待查询结束后再试。'

    def select_platform(self, pid):
        self.selected = pid
        self.draw()
        self.save_preferences()

    def use_follow(self):
        self.manual = False
        self.save_preferences()

    def use_manual(self):
        self.manual = True
        self.save_preferences()

    def show_menu(self, event):
        menu = self.tk.Menu(self.root, tearoff=False, bg='#ffffff', fg='#273772', activebackground='#e9eefc', activeforeground='#273772')
        for balance in self.data['balances']:
            menu.add_radiobutton(label=balance['name'], value=balance['id'],
                                 command=lambda pid=balance['id']: self.select_platform(pid))
        if self.data['balances']:
            menu.add_separator()
        menu.add_command(label='跟随宠物（拖动结束后更新）', command=self.use_follow)
        menu.add_command(label='手动摆放（拖动气泡）', command=self.use_manual)
        menu.add_command(label='查看状态 / 配置提示', command=self.show_info)
        menu.add_separator()
        menu.add_command(label='关闭气泡', command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def show_info(self):
        from tkinter import messagebox
        summary = bubble_summary(self.data, self.selected)
        message = '\n'.join([
            self.location.get('reason', ''),
            '余额单位：' + (summary['unit'] or '未配置'),
            '费用单位：' + (summary['cost_unit'] or '未配置'),
            '余额上次成功：' + (summary['success_at'] or '无'),
            '今日 Token：本机 Codex 桌面端总用量，不受余额平台选择影响。',
            '今日估算：所选平台已映射且已配置价格的 Codex 用量。',
            summary['message'],
            summary['cost_message'],
            '消费上次成功：' + (summary['cost_success_at'] or '无'),
            '今日消费（若启用）：平台账号范围，包含其他软件调用。',
            '在 Codex 中说“配置用量宠物”设置余额接口和模型价格。',
            '在 Codex 中说“查看用量统计”打开完整图表。'])
        messagebox.showinfo('Codex 用量宠物', message, parent=self.root)

    def close(self):
        self.visible = False
        self.click_monitor.close()
        self.publish('stopped')
        self.root.destroy()


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=PLUGIN_ROOT / '.local/config.local.json')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--manual', action='store_true')
    parser.add_argument('--duration', type=float, default=0)
    args = parser.parse_args()
    lock = InstanceLock(args.config.parent / 'bubble.instance.lock')
    if not lock.acquire():
        return
    instance = uuid.uuid4().hex
    try:
        enable_dpi_awareness()
        bubble = Bubble(args, instance)
        bubble.root.mainloop()
    except Exception as error:
        atomic_json(args.config.parent / 'bubble.status.json', {
            'instance': instance, 'pid': os.getpid(), 'state': 'failed',
            'visible': False, 'error': '气泡运行异常：' + type(error).__name__})
    finally:
        lock.release()


if __name__ == '__main__':
    run()

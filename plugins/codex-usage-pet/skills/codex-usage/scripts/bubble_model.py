"""Presentation and placement rules independent of desktop APIs."""
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import PureWindowsPath

from pricing import decimal
from collector import parse_offset


class PetClickGate:
    """Recognize a short click, excluding drag excursions, and time visibility."""
    def __init__(self):
        self.press = None
        self.until = 0

    def cancel(self):
        self.press = None
        self.until = 0

    def visible(self, now):
        return now < self.until

    def observe(self, event, point, hit, now):
        if event == 'down':
            self.press = (point, now) if hit else None
        elif self.press:
            origin, started = self.press
            if max(abs(point[i] - origin[i]) for i in (0, 1)) > 6:
                self.press = None
            elif event == 'up':
                self.press = None
                if hit and now - started <= 0.7:
                    self.until = now + 5
                    return True
        return False


def token_text(value):
    return f'{value / 1_000_000:.2f}'.rstrip('0').rstrip('.') + 'M' if value >= 1_000_000 else f'{value:,}'


def amount_text(value):
    return str(decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def balance_time(value, offset):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return ''
        return stamp.astimezone(parse_offset(offset)).strftime('%H:%M:%S')
    except (AttributeError, TypeError, ValueError):
        return ''


def bubble_summary(data, platform_id, today=None):
    balance = next((b for b in data.get('balances', []) if b['id'] == platform_id), {})
    today_rows = [r for r in data.get('records', []) if r['day'] == data.get('today')]
    rows = [r for r in today_rows if platform_id is None or r.get('platform_id') == platform_id]
    units = {r['cost']['unit'] for r in rows if r.get('cost')}
    cost = '—'
    if platform_id is not None and rows and not any(not r.get('cost') for r in rows) and len(units) == 1:
        cost = amount_text(sum((decimal(r['cost']['amount']) for r in rows), Decimal(0)))
    cost_label = cost if cost != '—' else '单位不一致' if len(units) > 1 else '价格不完整' if units else '未配置价格'
    actual = data.get('cost_source') == 'platform'
    spending = next((s for s in data.get('daily_spending', []) if s['id'] == platform_id), {}) if actual else {}
    if actual:
        valid = spending.get('day') == data.get('today') and spending.get('amount') is not None
        cost = amount_text(spending['amount']) if valid else '—'
        cost_label = cost + ('（旧）' if spending.get('state') == 'stale' else '') if valid else '查询失败' if spending.get('state') == 'error' else '待查询'
        units = {spending['unit']} if spending.get('unit') else set()
    current = bool(data.get('today')) and (today is None or today == data['today'])
    status = {'stale': '旧余额', 'error': '查询异常', 'unconfigured': '接口未配置'}.get(balance.get('state'), '')
    if not balance:
        status = '未配置平台' if platform_id is None else '等待查询'
    return {'name': balance.get('name') or ('Codex' if platform_id is None else platform_id),
            'balance': amount_text(balance['amount']) if balance.get('amount') is not None else '—',
            'cost': cost if current else '—',
            'cost_label': cost_label if current else '待更新',
            'cost_title': '今日消费' if actual else '今日估算',
            'cost_message': spending.get('message', ''),
            'cost_success_at': spending.get('success_at', ''),
            'tokens': token_text(sum(r['total_tokens'] for r in today_rows)) if current else '—',
            'status': status, 'current': current,
            'stale': balance.get('state') == 'stale', 'unit': balance.get('unit', ''),
            'cost_unit': next(iter(units), ''), 'message': balance.get('message', ''),
            'success_at': balance.get('success_at', '')}


def bubble_position(anchor, work_area, size):
    ax, ay, aw, ah = anchor
    left, top, right, bottom = work_area
    width, height = size
    x = round(ax + aw / 2 - width / 2)
    y = round(ay - height - 8)
    if y < top:
        y = round(ay + ah + 8)
    return (max(left, min(x, right - width)), max(top, min(y, bottom - height)))


def saved_anchor(bounds, physical_monitor):
    try:
        display = bounds['displayBounds']
        x, y, width, height = [float(v) for v in (bounds['x'], bounds['y'], display['width'], display['height'])]
        if not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
            return None
        left, top, right, bottom = physical_monitor
        sx, sy = (right - left) / width, (bottom - top) / height
        dx, dy = float(display.get('x', 0)), float(display.get('y', 0))
        result = (round(left + (x - dx) * sx), round(top + (y - dy) * sy), round(112 * sx), round(121 * sy))
        if not left - 1 <= result[0] < right or not top - 1 <= result[1] < bottom:
            return None
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def choose_overlay(windows):
    candidates = []
    for window in windows:
        path = str(window.get('path', '')).replace('\\', '/').lower()
        basename = PureWindowsPath(path).name
        codex = '/openai.codex_' in path or ('/openai/codex/' in path) or basename == 'codex.exe'
        style, extended = window.get('style', 0), window.get('exstyle', 0)
        if not codex or not window.get('visible') or window.get('class') != 'Chrome_WidgetWin_1':
            continue
        # Layered + topmost + toolwindow, and no normal titlebar.
        if extended & 0x80088 != 0x80088 or style & 0x00c00000:
            continue
        candidates.append(window)
    return candidates[0] if len(candidates) == 1 else None

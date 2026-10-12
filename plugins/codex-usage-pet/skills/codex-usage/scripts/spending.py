"""Actual account daily spending, independent of Codex-only token estimates."""
from datetime import datetime, time, timezone
from decimal import Decimal
from urllib.parse import urlencode

from balance import QueryError, query_all, quota_amount, request_json
from collector import parse_offset
from credentials import read_secret
from pricing import decimal


def day_window(now):
    start = datetime.combine(now.date(), time.min, now.tzinfo)
    return int(start.timestamp()), int(now.timestamp())


def aggregate_today(rows, status, now):
    if not isinstance(rows, list):
        raise QueryError('今日消费响应格式已变化。')
    start, end = day_window(now)
    total = Decimal(0)
    try:
        for row in rows:
            timestamp = decimal(row['created_at'])
            if timestamp < 0 or timestamp != timestamp.to_integral_value():
                raise ValueError
            if start <= timestamp <= end:
                quota = decimal(row['quota'])
                if quota < 0:
                    raise ValueError
                total += quota
    except (KeyError, ValueError, TypeError):
        raise QueryError('今日消费记录无法解析。') from None
    result = quota_amount(total, status)
    result.update({'day': now.date().isoformat(), 'note': '平台账号今日消费，包含其他软件调用。'})
    return result


def query_spending(platform, directory, now):
    spec = platform.get('balance', {})
    if spec.get('adapter') != 'penguin' or spec.get('url', 'https://penguinapi.org').rstrip('/') != 'https://penguinapi.org':
        raise QueryError('此平台尚未配置经过核实的今日消费接口。')
    try:
        headers = {'Authorization': 'Bearer ' + read_secret(directory, platform['id'])}
    except ValueError:
        raise QueryError('未配置平台访问令牌或无法解密。') from None
    user_id = spec.get('user_id')
    if user_id:
        if not str(user_id).isascii() or not str(user_id).isdigit() or int(user_id) < 1:
            raise QueryError('用户 ID 应为正整数。')
        headers['New-Api-User'] = str(user_id)
    start, end = day_window(now)
    params = urlencode({'start_timestamp': start, 'end_timestamp': end, 'time_granularity': 'hour'})
    payload = request_json('https://penguinapi.org/api/data/self?' + params, headers)
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise QueryError('平台未返回成功的今日消费记录。')
    response = request_json('https://penguinapi.org/api/status')
    status = response.get('data', {}) if isinstance(response, dict) and response.get('success') is True else {}
    return aggregate_today(payload.get('data'), status, now)


def query_all_spending(platforms, directory, offset='+08:00', now=None):
    instant = datetime.now(timezone.utc).timestamp() if now is None else now
    local = datetime.fromtimestamp(instant, parse_offset(offset))
    return query_all(platforms, directory, lambda platform, folder: query_spending(platform, folder, local),
                     now=instant, cache_name='daily-spending.local.json',
                     scope={'day': local.date().isoformat(), 'offset': offset})

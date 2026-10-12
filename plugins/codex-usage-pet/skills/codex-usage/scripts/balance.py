"""Explicit read-only adapters; preserve last successful balance on failures."""
import json
import time
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from credentials import atomic_json, read_secret
from pricing import decimal


class QueryError(Exception):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError
        if any(c.isspace() for c in url):
            raise ValueError
        if any(word in parts.query.lower() for word in ("token=", "key=", "secret=", "password=")):
            raise ValueError
    except (TypeError, ValueError):
        raise QueryError("余额接口必须是无凭证、无片段的 HTTPS URL。") from None


def request_json(url, headers=None):
    validate_url(url)
    values = {"Accept": "application/json", "User-Agent": "CodexUsagePet/2.0"}
    values.update(headers or {})
    if any(not isinstance(k, str) or not isinstance(v, str) or "\n" in k + v or "\r" in k + v for k, v in values.items()):
        raise QueryError("请求头格式无效。")
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=values, method="GET"), timeout=12) as response:
            raw = response.read(1_048_577)
    except HTTPError as error:
        if error.code in (401, 403):
            raise QueryError("认证失效或权限不足，请重新配置余额令牌。", 60) from None
        if error.code == 429:
            retry = error.headers.get("Retry-After", "60")
            retry = max(60, int(retry)) if retry.isdigit() else 60
            raise QueryError("平台限流，请稍后查询。", retry) from None
        if 300 <= error.code < 400:
            raise QueryError("平台返回重定向，请按文档更新接口地址。") from None
        raise QueryError(f"平台查询失败（HTTP {error.code}）。") from None
    except (URLError, OSError, ValueError):
        raise QueryError("网络连接失败、请求超时或配置无效。") from None
    if len(raw) > 1_048_576:
        raise QueryError("平台响应过大。")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise QueryError("平台没有返回有效 JSON。") from None


def field_value(payload, path):
    if not isinstance(path, list) or not path:
        raise QueryError("请按真实接口配置余额字段路径。")
    try:
        for key in path:
            if isinstance(payload, dict) and isinstance(key, str):
                payload = payload[key]
            elif isinstance(payload, list) and isinstance(key, int) and not isinstance(key, bool) and key >= 0:
                payload = payload[key]
            else:
                raise ValueError
        return payload
    except (KeyError, IndexError, TypeError, ValueError):
        raise QueryError("接口字段已变化或余额字段路径不正确。") from None


def query_balance(platform, directory):
    spec = platform.get("balance")
    if not isinstance(spec, dict):
        raise QueryError("尚未配置余额接口。")
    adapter = spec.get("adapter")
    headers = dict(spec.get("headers", {}))
    if any(k.lower() in ("authorization", "cookie", "x-api-key", "api-key") for k in headers):
        raise QueryError("认证值须通过本地加密令牌配置，不能写在 headers。")
    auth = spec.get("auth")
    if adapter == "penguin":
        auth = {"header": "Authorization", "prefix": "Bearer "}
    if auth:
        header = auth.get("header")
        if not isinstance(header, str) or not header or header.lower() in (k.lower() for k in headers):
            raise QueryError("认证头配置无效。")
        try:
            headers[header] = str(auth.get("prefix", "")) + read_secret(directory, platform["id"])
        except ValueError:
            raise QueryError("未配置余额令牌或本地令牌无法解密。") from None
    if adapter == "json_get":
        if not spec.get("documentation_url") or not spec.get("unit"):
            raise QueryError("请补充接口依据和明确的余额单位。")
        payload = request_json(spec.get("url"), headers)
        if "success_field" in spec and field_value(payload, spec["success_field"]) != spec.get("success_value", True):
            raise QueryError("平台未返回成功状态，请核对认证和接口。")
        try:
            raw = decimal(field_value(payload, spec.get("field")))
            divisor = decimal(spec.get("divisor", "1"))
            if divisor <= 0:
                raise ValueError
        except ValueError:
            raise QueryError("余额或换算规则不是有效数字。") from None
        return {"amount": str(raw / divisor), "unit": spec["unit"], "note": "平台账号余额"}
    if adapter != "penguin":
        raise QueryError("未知余额适配器，请使用已确认的接口。")
    base = spec.get("url", "https://penguinapi.org").rstrip("/")
    if base != "https://penguinapi.org":
        raise QueryError("企鹅适配器仅用于已核实的 penguinapi.org。")
    user_id = spec.get("user_id", "")
    if user_id:
        if not str(user_id).isascii() or not str(user_id).isdigit() or int(user_id) < 1:
            raise QueryError("用户 ID 应为正整数。")
        headers["New-Api-User"] = str(user_id)
    account = request_json(base + "/api/user/self", headers)
    if not isinstance(account, dict) or account.get("success") is not True:
        raise QueryError("平台未返回成功的账户信息。")
    data = account.get("data") or {}
    if user_id and str(data.get("id")) != str(user_id):
        raise QueryError("返回账户与配置的用户 ID 不一致。")
    try:
        quota = decimal(data.get("quota"))
    except ValueError:
        raise QueryError("账户余额字段已变化。") from None
    try:
        status = request_json(base + "/api/status")
        status = status.get("data") if status.get("success") is True else {}
    except QueryError:
        status = {}
    return quota_amount(quota, status)


def quota_amount(quota, status):
    result = {"amount": str(quota), "unit": "原始额度", "note": "未获取有效单位规则，保留原始额度。"}
    try:
        mode = status.get("quota_display_type")
        if mode == "TOKENS":
            return {"amount": str(quota), "unit": "Token", "note": "平台账号余额"}
        if status.get("display_in_currency") is False:
            return result
        divisor = decimal(status.get("quota_per_unit"))
        if divisor <= 0:
            return result
        if mode == "USD":
            return {"amount": str(quota / divisor), "unit": "USD", "note": "平台账号余额"}
        key = "usd_exchange_rate" if mode == "CNY" else "custom_currency_exchange_rate"
        unit = "CNY" if mode == "CNY" else status.get("custom_currency_symbol")
        if mode in ("CNY", "CUSTOM") and isinstance(unit, str) and unit:
            rate = decimal(status.get(key))
            if rate > 0:
                return {"amount": str(quota / divisor * rate), "unit": unit, "note": "平台账号余额"}
    except (QueryError, ValueError, AttributeError):
        pass
    return result


def query_all(platforms, directory, request_fn=query_balance, now=None,
              cache_name='balances.local.json', scope=None):
    now = time.time() if now is None else now
    cache_path = directory / cache_name
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    def one(platform):
        pid = platform["id"]
        # Changing endpoint/metadata invalidates old cached account balances.
        identity = platform.get('balance') if scope is None else [platform.get('balance'), scope]
        signature = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        old = cache.get(pid, {})
        if not isinstance(old, dict):
            old = {}
        if old.get("signature") != signature:
            old = {}
        if not platform.get("balance"):
            return pid, {"state": "unconfigured", "message": "未配置余额接口。", "signature": signature}
        if old.get("next_query_at", 0) > now:
            return pid, old
        try:
            result = request_fn(platform, directory)
            return pid, {"state": "success", **result, "signature": signature,
                         "success_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                         "next_query_at": now + 60}
        except QueryError as error:
            return pid, {**old, "state": "stale" if "amount" in old else "error",
                         "message": str(error), "signature": signature,
                         "next_query_at": now + error.retry_after}
        except (OSError, ValueError, TypeError, AttributeError):
            return pid, {**old, "state": "stale" if "amount" in old else "error",
                         "message": "平台配置或响应格式无效。", "signature": signature, "next_query_at": now + 60}
    if not platforms:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(platforms))) as pool:
        cache = dict(pool.map(one, platforms))
    atomic_json(cache_path, cache)
    return [{"id": p["id"], "name": p["name"],
             **{k: v for k, v in cache[p["id"]].items() if k not in ("signature", "next_query_at")}} for p in platforms]

"""Per-platform estimates; no account spending is attributed to Codex."""
from decimal import Decimal, InvalidOperation


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("应为数字。")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("应为数字。") from None
    if not result.is_finite():
        raise ValueError("应为有限数字。")
    return result


def estimate(record, price):
    if not isinstance(price, dict) or not price.get("unit"):
        return None
    counts = {"input": record["input_tokens"] - record["cached_input_tokens"],
              "cached_input": record["cached_input_tokens"],
              "cache_write_input": record["cache_write_input_tokens"],
              "output": record["output_tokens"]}
    if counts["cache_write_input"]:
        if not isinstance(price.get("cache_write_in_input"), bool):
            return None
        if price["cache_write_in_input"]:
            counts["input"] -= counts["cache_write_input"]
    total = Decimal(0)
    try:
        for category, count in counts.items():
            if count < 0:
                return None
            if count:
                rate = decimal(price[category])
                if rate < 0:
                    return None
                total += Decimal(count) * rate / Decimal(1_000_000)
    except (KeyError, ValueError):
        return None
    return {"amount": str(total), "unit": price["unit"]}


def add_prices(data, config):
    platforms = {p["id"]: p for p in config.get("platforms", [])}
    for record in data["records"]:
        platform_id = config.get("providers", {}).get(record["provider"])
        record["platform_id"] = platform_id
        platform = platforms.get(platform_id, {})
        record["platform_name"] = platform.get("name", "未映射平台")
        record["cost"] = estimate(record, platform.get("prices", {}).get(record["model"]))
    return data

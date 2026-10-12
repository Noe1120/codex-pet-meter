"""Codex invokes reports and manages the optional desktop bubble."""
import argparse
import json
import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from balance import query_all
from collector import collect, parse_offset
from credentials import atomic_json, prompt_secret
from pricing import add_prices
from spending import query_all_spending

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = {"timezone_offset": "+08:00", "providers": {}, "platforms": []}


@contextmanager
def report_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".report.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("已有查询正在运行，请等待完成。") from None
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else dict(DEFAULT_CONFIG)
    if not isinstance(config, dict):
        raise ValueError("配置应为 JSON 对象。")
    parse_offset(config.get("timezone_offset", "+08:00"))
    platforms = config.get("platforms", [])
    if not isinstance(platforms, list) or not isinstance(config.get("providers", {}), dict):
        raise ValueError("platforms 应为列表，providers 应为对象。")
    ids = []
    for platform in platforms:
        if not isinstance(platform, dict):
            raise ValueError("平台配置应为对象。")
        pid = platform.get("id")
        if not isinstance(pid, str) or not pid or not isinstance(platform.get("name"), str):
            raise ValueError("每个平台需要非空 id 和 name。")
        if not isinstance(platform.get("prices", {}), dict):
            raise ValueError("模型价格应为对象。")
        ids.append(pid)
    if len(ids) != len(set(ids)) or any(v not in ids for v in config.get("providers", {}).values()):
        raise ValueError("平台 ID 不能重复，provider 必须映射到已配置平台。")
    bubble = config.get('bubble', {})
    if not isinstance(bubble, dict):
        raise ValueError('bubble 应为对象。')
    if 'manual' in bubble and not isinstance(bubble['manual'], bool):
        raise ValueError('manual 应为布尔值。')
    position = bubble.get('position')
    if position is not None and (not isinstance(position, list) or len(position) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   or abs(v) > 100000 for v in position)):
        raise ValueError('position 应为两个有限屏幕坐标。')
    interval = bubble.get('refresh_seconds', 60)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 60 <= interval <= 3600:
        raise ValueError('refresh_seconds 应为 60–3600 的整数。')
    if bubble.get('platform_id') is not None and bubble['platform_id'] not in ids:
        raise ValueError('bubble.platform_id 必须是已配置平台。')
    if bubble.get('display', 'click') not in ('click', 'always'):
        raise ValueError('bubble.display 应为 click 或 always。')
    if bubble.get('cost_source', 'estimate') not in ('estimate', 'platform'):
        raise ValueError('bubble.cost_source 应为 estimate 或 platform。')
    return config


def write_panel(data, target):
    template = (PLUGIN_ROOT / "skills/codex-usage/assets/panel.html").read_text(encoding="utf-8")
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template.replace("__USAGE_DATA__", serialized), encoding="utf-8")
    return target.resolve()


def demo_data():
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    now = datetime.now(timezone(timedelta(hours=8)))
    records = []
    for n in range(7):
        day = (now - timedelta(days=6 - n)).date().isoformat()
        for i, model in enumerate(("模型 A", "模型 B", "模型 C")):
            tokens = (n + 1) * (i + 1) * 21_357
            records.append({"day": day, "model": model, "provider": "demo",
                            "platform_id": "demo", "platform_name": "演示平台",
                            "total_tokens": tokens, "input_tokens": tokens // 2,
                            "output_tokens": tokens - tokens // 2, "cached_input_tokens": 0,
                            "cache_write_input_tokens": 0, "reasoning_output_tokens": 0,
                            "cost": {"amount": str(Decimal(tokens) / Decimal(1_000_000)), "unit": "USD"}})
    return {"today": now.date().isoformat(), "generated_at": now.isoformat(timespec="seconds"),
            "records": records, "session_count": 0, "warnings": ["模拟数据，仅用于界面演示。"],
            "demo": True, "balances": [{"id": "demo", "name": "演示平台", "state": "success",
                                        "amount": "18.25", "unit": "USD", "success_at": now.isoformat(), "note": "模拟余额"}]}


def run():
    parser = argparse.ArgumentParser(description="Codex 桌面端用量统计插件")
    parser.add_argument("command", choices=("init", "report", "set-secret", "demo", "bubble-start", "bubble-stop", "bubble-status"))
    parser.add_argument("--config", type=Path, default=PLUGIN_ROOT / ".local/config.local.json")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--platform")
    parser.add_argument("--offline", action="store_true", help="不查询平台余额，只生成本地 Token 报告")
    parser.add_argument("--bubble-demo", action="store_true", help="气泡使用明确标记的模拟数据，不查询余额")
    parser.add_argument("--manual", action="store_true", help="气泡手动摆放，不跟随原生宠物")
    args = parser.parse_args()
    if args.command.startswith('bubble-'):
        from bubble_control import start, stop, status
        if args.command == 'bubble-start':
            load_config(args.config)
            result = start(args.config, args.codex_home, demo=args.bubble_demo, manual=args.manual)
        elif args.command == 'bubble-stop':
            result = stop(args.config.parent)
        else:
            result = status(args.config.parent)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "init":
        if not args.config.exists():
            atomic_json(args.config, DEFAULT_CONFIG)
        print(json.dumps({"config_path": str(args.config.resolve()), "credentials": "本地 DPAPI 加密；配置不包含密钥"}, ensure_ascii=False))
        return
    config = load_config(args.config)
    if args.command == "set-secret":
        platform = next((p for p in config.get("platforms", []) if p["id"] == args.platform), None)
        if platform is None:
            raise ValueError("请先在配置中添加所选平台。")
        with report_lock(args.config.parent):
            saved = prompt_secret(args.config.parent, platform["id"], platform["name"])
        print("本地令牌已加密保存。" if saved else "已取消，未更改令牌。")
        return
    with report_lock(args.config.parent):
        if args.command == "demo":
            data = demo_data()
        else:
            data = add_prices(collect(args.codex_home, offset=config.get("timezone_offset", "+08:00")), config)
            if args.offline:
                data["balances"] = [{"id": p["id"], "name": p["name"], "state": "unconfigured", "message": "本次未查询余额。"} for p in config.get("platforms", [])]
            else:
                data["balances"] = query_all(config.get("platforms", []), args.config.parent)
                data['cost_source'] = config.get('bubble', {}).get('cost_source', 'estimate')
                if data['cost_source'] == 'platform':
                    data['daily_spending'] = query_all_spending(config.get('platforms', []), args.config.parent,
                                                              offset=config.get('timezone_offset', '+08:00'))
            if not config.get("platforms"):
                data["warnings"].append("未配置平台；Token 可查看，费用和余额尚不可用。")
            data["warnings"].append("本机桌面端可读取历史；不含其他设备或已删除记录。")
        target = args.output or args.config.parent / ("demo.html" if args.command == "demo" else "codex-usage.html")
        path = write_panel(data, target)
    total = sum(r["total_tokens"] for r in data["records"])
    today = sum(r["total_tokens"] for r in data["records"] if r["day"] == data["today"])
    print(json.dumps({"panel_path": str(path), "today_tokens": today, "total_tokens": total,
                      "coverage": data["warnings"], "demo": data.get("demo", False)}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        run()
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Errors are deliberately generic: configuration may contain sensitive values.
        print(json.dumps({"error": "本地配置、数据或文件操作失败，请检查格式、运行环境及目录权限。", "type": type(error).__name__}, ensure_ascii=False))
        sys.exit(1)

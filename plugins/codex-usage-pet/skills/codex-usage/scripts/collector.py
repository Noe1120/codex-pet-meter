"""Read desktop-origin rollout counters without exporting conversation content."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens", "total_tokens")


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def parse_usage(value):
    if not isinstance(value, dict) or not all(k in value for k in ("input_tokens", "output_tokens", "total_tokens")):
        return None
    result = {k: value.get(k, 0) for k in COUNTERS}
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in result.values()):
        return None
    if result["cached_input_tokens"] > result["input_tokens"]:
        return None
    return result


@dataclass
class Session:
    id: str
    originator: str
    provider: str
    base: dict
    events: list = field(default_factory=list)


def read_session(path, warnings):
    session = None
    try:
        with path.open("rb") as stream:
            ordinal = 0
            offset = 0
            for line in stream:
                # Only metadata/counters are retained, never message bodies.
                if any(t in line for t in (b'"session_meta"', b'"turn_context"', b'"token_count"')):
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        warnings.add("部分会话记录不完整，无法解析的事件已跳过。")
                        ordinal += 1
                        offset += len(line)
                        continue
                    payload = item.get("payload")
                    if not isinstance(payload, dict):
                        ordinal += 1
                        offset += len(line)
                        continue
                    kind = item.get("type")
                    if kind == "session_meta" and session is None:
                        sid = payload.get("id") or payload.get("session_id")
                        if isinstance(sid, str):
                            session = Session(sid, str(payload.get("originator", "")),
                                              str(payload.get("model_provider", "未知平台")),
                                              payload.get("history_base") or {})
                    elif session and (kind == "turn_context" or (kind == "event_msg" and payload.get("type") == "token_count")):
                        if kind == "turn_context":
                            safe = {k: payload.get(k) for k in ("model", "turn_id")}
                        else:
                            info = payload.get("info") or {}
                            safe = {"total": parse_usage(info.get("total_token_usage")),
                                    "last": parse_usage(info.get("last_token_usage"))}
                        session.events.append((ordinal, offset, kind, item.get("timestamp"), safe))
                ordinal += 1
                offset += len(line)
    except OSError:
        warnings.add("部分会话文件无法读取，统计可能不完整。")
    return session


def rollout_paths(home, warnings):
    paths = set()
    for folder in ("sessions", "archived_sessions"):
        directory = home / folder
        if directory.exists():
            paths.update(directory.rglob("*.jsonl"))
    database = home / "state_5.sqlite"
    if database.exists():
        try:
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
                for (raw,) in connection.execute("SELECT rollout_path FROM threads"):
                    path = Path(raw)
                    if path.is_file():
                        paths.add(path)
                    else:
                        warnings.add("索引中的部分历史已不在本机，累计量只包含可读取记录。")
        except sqlite3.Error:
            warnings.add("会话索引暂不可读，已使用本地会话目录。")
    return sorted(paths, key=lambda p: str(p))


def inherited_state(session, sessions, warnings):
    if not session.base:
        return {k: 0 for k in COUNTERS}, "未知模型", None
    if not isinstance(session.base, dict):
        warnings.add("分叉会话的历史结构未知，已排除以避免重复统计。")
        return None
    parent = sessions.get(session.base.get("thread_id"))
    if parent is None:
        warnings.add("部分分叉缺少原始历史，已排除以避免重复统计。")
        return None
    limit = session.base.get("end_ordinal_exclusive")
    byte_limit = session.base.get("end_byte_offset")
    if not isinstance(limit, int) and not isinstance(byte_limit, int):
        warnings.add("分叉边界未知，已排除以避免重复统计。")
        return None
    previous, model, turn = {k: 0 for k in COUNTERS}, "未知模型", None
    for ordinal, offset, kind, _, payload in parent.events:
        if (isinstance(limit, int) and ordinal >= limit) or (isinstance(byte_limit, int) and offset >= byte_limit):
            break
        if kind == "turn_context":
            model = payload.get("model") or "未知模型"
            turn = payload.get("turn_id")
        elif payload.get("total"):
            previous = payload["total"]
    return previous, model, turn


def collect(home: Path, now=None, offset="+08:00"):
    warnings = set()
    sessions = {}
    for path in rollout_paths(home, warnings):
        session = read_session(path, warnings)
        if session:
            existing = sessions.get(session.id)
            if existing:
                existing.events.extend(session.events)
            else:
                sessions[session.id] = session
    zone = parse_offset(offset)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(zone).date().isoformat()
    records, seen, desktop_ids = [], set(), set()
    ordered, visiting, added = [], set(), set()
    def visit(session):
        if session.id in added:
            return
        if session.id in visiting:
            warnings.add("发现循环分叉关系，相关历史需要人工检查。")
            return
        visiting.add(session.id)
        parent = sessions.get(session.base.get("thread_id")) if isinstance(session.base, dict) else None
        if parent:
            visit(parent)
        visiting.discard(session.id)
        added.add(session.id)
        ordered.append(session)
    for session in sessions.values():
        visit(session)
    for session in ordered:
        if session.originator != "Codex Desktop":
            if not session.originator:
                warnings.add("来源不明确的会话已排除，不计入桌面端用量。")
            continue
        state = inherited_state(session, sessions, warnings)
        if state is None:
            continue
        desktop_ids.add(session.id)
        previous, model, turn = state
        first_snapshot = not session.base
        # Stable ordering also handles resumed copies of the same session.
        for _, _, kind, stamp, payload in sorted(session.events, key=lambda e: (e[3] or "", e[0])):
            if kind == "turn_context":
                model = payload.get("model") or "未知模型"
                turn = payload.get("turn_id")
                continue
            current = payload.get("total")
            if current is None:
                continue
            fingerprint = (stamp, turn, model, session.provider, tuple(current.values()))
            if fingerprint in seen:
                if all(current[k] >= previous[k] for k in COUNTERS):
                    previous = current
                continue
            seen.add(fingerprint)
            delta = {k: current[k] - previous[k] for k in COUNTERS}
            if any(v < 0 for v in delta.values()):
                warnings.add("发现回退的累计计数，相关区间已跳过，避免误计重置。")
                continue
            if first_snapshot and payload.get("last") and current != payload["last"]:
                delta = payload["last"]
                warnings.add("部分首条快照包含缺失历史；只计可归属本次的最后调用。")
            first_snapshot = False
            previous = current
            timestamp = parse_time(stamp)
            if timestamp is None:
                warnings.add("部分事件缺少有效时间，已排除。")
                continue
            if delta["total_tokens"] == 0:
                continue
            records.append({"day": timestamp.astimezone(zone).date().isoformat(),
                            "timestamp": timestamp.isoformat(), "model": model,
                            "provider": session.provider, **delta})
    records.sort(key=lambda r: r["timestamp"])
    return {"today": today, "records": records, "session_count": len(desktop_ids),
            "warnings": sorted(warnings), "generated_at": now.astimezone(zone).isoformat(timespec="seconds")}


def parse_offset(value):
    try:
        if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
            raise ValueError
        hours, minutes = int(value[1:3]), int(value[4:6])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError
        return timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if value[0] == "+" else -1)))
    except (TypeError, ValueError):
        raise ValueError("timezone_offset 应为 +08:00 这样的固定时区偏移。") from None

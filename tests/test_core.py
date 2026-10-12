import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/codex-usage-pet/skills/codex-usage/scripts"
sys.path.insert(0, str(SCRIPTS))
from collector import collect, parse_usage
from pricing import estimate, add_prices
from balance import query_all, QueryError, request_json, field_value, query_balance
from credentials import crypt, save_secret, read_secret
from main import load_config, write_panel, report_lock

WORK = ROOT / ".research/test-work"
WORK.mkdir(parents=True, exist_ok=True)


def usage(input_tokens, output_tokens=0, cached=0, writes=0, reasoning=0):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_input_tokens": cached, "cache_write_input_tokens": writes,
            "reasoning_output_tokens": reasoning, "total_tokens": input_tokens + output_tokens}


def context(model="model-a", turn="turn-1", stamp="2026-10-10T10:00:00Z"):
    return {"type": "turn_context", "timestamp": stamp, "payload": {"model": model, "turn_id": turn}}


def token(total, last=None, stamp="2026-10-10T10:01:00Z"):
    return {"type": "event_msg", "timestamp": stamp,
            "payload": {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": last or total}}}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def session(self, sid, events, originator="Codex Desktop", base=None, name=None):
        meta = {"type": "session_meta", "payload": {"id": sid, "originator": originator,
                "source": "vscode", "model_provider": "relay", "history_base": base}}
        path = self.home / "sessions" / (name or sid + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(x) for x in [meta, *events]) + "\n", encoding="utf-8")
        return path

    def data(self):
        return collect(self.home, datetime(2026, 10, 11, 1, tzinfo=timezone.utc))

    def test_snapshot_duplicates_are_not_calls(self):
        a, b = usage(100, 20), usage(150, 35)
        self.session("s", [context(), token(a), token(a), token(b, usage(50, 15), "2026-10-10T10:03:00Z")])
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 185)

    def test_desktop_origin_not_source_is_the_filter(self):
        self.session("s", [context(), token(usage(100))])
        self.session("cli", [context(), token(usage(1000))], "Codex CLI")
        self.session("unknown", [context(), token(usage(9000))], "")
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 100)
        self.assertTrue(self.data()["warnings"])

    def test_day_boundary_and_model_switch(self):
        self.session("s", [context(stamp="2026-10-10T15:58:00Z"), token(usage(100), stamp="2026-10-10T15:59:00Z"),
            context("model-b", "turn-2", "2026-10-10T16:00:00Z"), token(usage(150), usage(50), "2026-10-10T16:01:00Z")])
        rows = self.data()["records"]
        self.assertEqual([(r["day"], r["model"], r["total_tokens"]) for r in rows],
                         [("2026-10-10", "model-a", 100), ("2026-10-11", "model-b", 50)])

    def test_fork_does_not_recount_parent(self):
        self.session("parent", [context(), token(usage(100))], name="z-parent.jsonl")
        self.session("child", [context("model-b", "turn-child"), token(usage(140), usage(40), "2026-10-10T11:00:00Z")],
                     base={"thread_id": "parent", "end_ordinal_exclusive": 3}, name="a-child.jsonl")
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 140)

    def test_missing_parent_excludes_fork(self):
        self.session("child", [context(), token(usage(100))], base={"thread_id": "missing", "end_ordinal_exclusive": 3})
        self.assertEqual(self.data()["records"], [])
        self.assertTrue(self.data()["warnings"])

    def test_archived_copy_of_same_session_is_not_counted_twice(self):
        path = self.session("s", [context(), token(usage(100))])
        archive = self.home / "archived_sessions"
        archive.mkdir()
        (archive / "copy.jsonl").write_bytes(path.read_bytes())
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 100)

    def test_truncated_first_snapshot_keeps_only_last_call(self):
        self.session("s", [context(), token(usage(1000), usage(20))])
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 20)
        self.assertTrue(self.data()["warnings"])

    def test_counter_regression_is_not_an_extra_call(self):
        self.session("s", [context(), token(usage(100)), token(usage(30), stamp="2026-10-10T11:00:00Z")])
        self.assertEqual(sum(r["total_tokens"] for r in self.data()["records"]), 100)
        self.assertTrue(self.data()["warnings"])

    def test_zero_usage_valid_and_bad_numeric_rejected(self):
        self.session("s", [context(), token(usage(0))])
        self.assertEqual(self.data()["records"], [])
        self.assertIsNone(parse_usage({"input_tokens": True, "output_tokens": 0, "total_tokens": 1}))

    def test_cached_input_and_reasoning_are_not_double_charged(self):
        result = estimate(usage(1_000_000, 100_000, 800_000, reasoning=50_000),
                          {"input": "2", "cached_input": "0.5", "output": "10", "unit": "USD"})
        self.assertEqual(result["amount"], "1.8")

    def test_missing_price_is_unknown_and_zero_price_is_allowed(self):
        self.assertIsNone(estimate(usage(100), {}))
        self.assertIsNone(estimate(usage(100, cached=20), {"input": "2", "unit": "USD"}))
        self.assertEqual(estimate(usage(100), {"input": "0", "unit": "USD"})["amount"], "0")

    def test_cache_write_requires_explicit_semantics(self):
        price = {"input": "2", "cache_write_input": "3", "unit": "USD"}
        self.assertIsNone(estimate(usage(1_000_000, writes=200_000), price))
        self.assertEqual(estimate(usage(1_000_000, writes=200_000), {**price, "cache_write_in_input": True})["amount"], "2.2")

    def test_provider_prices_remain_separate(self):
        data = {"records": [{"provider": "a", "model": "m", **usage(1_000_000)}, {"provider": "b", "model": "m", **usage(1_000_000)}]}
        config = {"providers": {"a": "p1", "b": "p2"}, "platforms": [
            {"id": "p1", "name": "A", "prices": {"m": {"input": "1", "unit": "USD"}}},
            {"id": "p2", "name": "B", "prices": {"m": {"input": "2", "unit": "CNY"}}}]}
        self.assertEqual([r["cost"]["unit"] for r in add_prices(data, config)["records"]], ["USD", "CNY"])

    def test_balance_zero_and_failure_preserve_success_time(self):
        platforms = [{"id": "p", "name": "A", "balance": {"adapter": "test"}}]
        result = query_all(platforms, self.home, lambda *_: {"amount": "0", "unit": "USD"}, now=1000)
        success_at = result[0]["success_at"]
        def fail(*_):
            raise QueryError("认证失效。")
        result = query_all(platforms, self.home, fail, now=1061)
        self.assertEqual(result[0]["state"], "stale")
        self.assertEqual(result[0]["amount"], "0")
        self.assertEqual(result[0]["success_at"], success_at)

    def test_balance_cooldown_and_endpoint_change(self):
        platforms = [{"id": "p", "name": "A", "balance": {"url": "a"}}]
        calls = []
        def fetch(*_):
            calls.append(1)
            return {"amount": "5", "unit": "USD"}
        query_all(platforms, self.home, fetch, now=1000)
        query_all(platforms, self.home, fetch, now=1001)
        self.assertEqual(len(calls), 1)
        platforms[0]["balance"]["url"] = "b"
        query_all(platforms, self.home, fetch, now=1002)
        self.assertEqual(len(calls), 2)

    def test_unconfigured_balance_does_not_request(self):
        result = query_all([{"id": "p", "name": "A"}], self.home, lambda *_: self.fail("network"))
        self.assertEqual(result[0]["state"], "unconfigured")

    def test_auth_and_rate_limit_errors_do_not_echo_server_body(self):
        for code in (401, 403, 429):
            error = HTTPError("https://example.invalid", code, "secret-should-not-leak", {"Retry-After": "120"}, None)
            with patch("balance.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(QueryError) as caught:
                    request_json("https://example.invalid/balance")
                self.assertNotIn("secret", str(caught.exception))
                if code == 429:
                    self.assertEqual(caught.exception.retry_after, 120)

    def test_network_and_redirect_errors(self):
        for error in (URLError("secret-data"), HTTPError("https://example.invalid", 302, "redirect", {}, None)):
            with patch("balance.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(QueryError):
                    request_json("https://example.invalid/balance")

    def test_invalid_url_never_sends_credentials(self):
        for url in ("http://example.invalid", "https://user:pass@example.invalid", "https://example.invalid/?token=secret"):
            with patch("balance.build_opener") as opener:
                with self.assertRaises(QueryError):
                    request_json(url, {"Authorization": "Bearer dummy"})
                opener.assert_not_called()

    def test_field_paths_and_missing_response(self):
        self.assertEqual(field_value({"rows": [{"balance": 0}]}, ["rows", 0, "balance"]), 0)
        with self.assertRaises(QueryError):
            field_value({"data": {}}, ["data", "balance"])

    def test_custom_adapter_uses_documented_divisor(self):
        platform = {"id": "p", "balance": {"adapter": "json_get", "url": "https://example.invalid/b",
                    "documentation_url": "https://example.invalid/docs", "field": ["remaining"], "unit": "额度", "divisor": "2"}}
        with patch("balance.request_json", return_value={"remaining": "100"}):
            self.assertEqual(query_balance(platform, self.home)["amount"], "50")

    def test_nonfinite_balance_is_an_error(self):
        platform = {"id": "p", "balance": {"adapter": "json_get", "url": "https://example.invalid/b",
                    "documentation_url": "https://example.invalid/docs", "field": ["b"], "unit": "USD"}}
        with patch("balance.request_json", return_value={"b": "NaN"}):
            with self.assertRaises(QueryError):
                query_balance(platform, self.home)

    def test_dpapi_roundtrip_uses_only_fake_secret(self):
        save_secret(self.home, "demo", "test-only-not-a-real-key")
        self.assertEqual(read_secret(self.home, "demo"), "test-only-not-a-real-key")
        self.assertNotIn("test-only-not-a-real-key", (self.home / "secrets.dpapi.json").read_text())

    def test_new_credential_does_not_retain_previous_account_balance(self):
        cache = self.home / "balances.local.json"
        cache.write_text(json.dumps({"demo": {"amount": "99"}, "other": {"amount": "8"}}))
        save_secret(self.home, "demo", "test-only-new-fake-credential")
        self.assertEqual(json.loads(cache.read_text()), {"other": {"amount": "8"}})

    def test_overlapping_reports_are_rejected(self):
        with report_lock(self.home):
            with self.assertRaises((ValueError, OSError)):
                with report_lock(self.home):
                    self.fail("overlapping query")

    def test_html_script_injection_is_escaped(self):
        path = self.home / "panel.html"
        write_panel({"model": "</script><script>leak()</script>"}, path)
        self.assertNotIn("<script>leak()", path.read_text(encoding="utf-8"))

    def test_duplicate_platform_config_rejected(self):
        path = self.home / "config.local.json"
        path.write_text(json.dumps({"platforms": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}))
        with self.assertRaises(ValueError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()

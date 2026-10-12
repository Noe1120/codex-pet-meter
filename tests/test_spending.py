"""Platform account daily spending stays separate from local Codex usage."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-usage-pet/skills/codex-usage/scripts'))
WORK = ROOT / '.research/test-work'
WORK.mkdir(parents=True, exist_ok=True)


class SpendingTests(unittest.TestCase):
    def test_only_today_is_summed_using_beijing_midnight(self):
        from spending import aggregate_today
        zone = timezone(timedelta(hours=8))
        now = datetime(2026, 10, 12, 15, tzinfo=zone)
        start = int(datetime(2026, 10, 12, tzinfo=zone).timestamp())
        rows = [{'created_at': start - 1, 'quota': 900000},
                {'created_at': start, 'quota': 500000},
                {'created_at': start + 3600, 'quota': 250000},
                {'created_at': int(now.timestamp()) + 1, 'quota': 900000}]
        result = aggregate_today(rows, {'quota_per_unit': 500000, 'quota_display_type': 'USD'}, now)
        self.assertEqual(result['amount'], '1.5')
        self.assertEqual(result['unit'], 'USD')
        self.assertEqual(result['day'], '2026-10-12')

    def test_zero_is_valid_and_bad_data_is_not_zero(self):
        from spending import aggregate_today
        from balance import QueryError
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.assertEqual(aggregate_today([], {}, now)['amount'], '0')
        for rows in (None, [{}], [{'created_at': now.timestamp(), 'quota': '-1'}],
                     [{'created_at': now.timestamp(), 'quota': 'NaN'}]):
            with self.subTest(rows=rows), self.assertRaises(QueryError):
                aggregate_today(rows, {}, now)

    def test_authenticated_request_uses_only_account_self_endpoint(self):
        from spending import query_spending
        now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        platform = {'id': 'p', 'balance': {'adapter': 'penguin', 'url': 'https://penguinapi.org'}}
        calls = []
        def request(url, headers=None):
            calls.append((url, headers))
            return {'success': True, 'data': [] if '/api/data/self?' in url else
                    {'quota_per_unit': 500000, 'quota_display_type': 'USD'}}
        with patch('spending.read_secret', return_value='dummy'), patch('spending.request_json', side_effect=request):
            result = query_spending(platform, Path('.'), now)
        self.assertEqual(result['amount'], '0')
        self.assertIn('/api/data/self?', calls[0][0])
        self.assertIn('time_granularity=hour', calls[0][0])
        self.assertEqual(calls[0][1]['Authorization'], 'Bearer dummy')

    def test_failure_preserves_only_same_day_and_respects_rate_limit(self):
        from spending import query_all_spending
        from balance import QueryError
        platform = {'id': 'p', 'name': 'P', 'balance': {'adapter': 'penguin'}}
        stamp = datetime(2026, 10, 12, 12, tzinfo=timezone.utc).timestamp()
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            directory = Path(temp)
            with patch('spending.query_spending', return_value={'amount': '2', 'unit': 'USD', 'day': '2026-10-12'}) as request:
                first = query_all_spending([platform], directory, now=stamp)
                query_all_spending([platform], directory, now=stamp + 10)
                self.assertEqual(request.call_count, 1)
            with patch('spending.query_spending', side_effect=QueryError('网络失败')):
                stale = query_all_spending([platform], directory, now=stamp + 61)[0]
                self.assertEqual(stale['amount'], '2')
                self.assertEqual(stale['state'], 'stale')
                self.assertEqual(stale['success_at'], first[0]['success_at'])
                tomorrow = query_all_spending([platform], directory, now=stamp + 86400)[0]
                self.assertEqual(tomorrow['state'], 'error')
                self.assertNotIn('amount', tomorrow)

    def test_bubble_actual_fee_keeps_codex_token_count_separate(self):
        from bubble_model import bubble_summary
        data = {'today': '2026-10-12', 'cost_source': 'platform', 'balances': [],
                'records': [{'day': '2026-10-12', 'total_tokens': 100, 'platform_id': None, 'cost': None}],
                'daily_spending': [{'id': 'p', 'day': '2026-10-12', 'amount': '1.239', 'unit': 'USD', 'state': 'success'}]}
        summary = bubble_summary(data, 'p')
        self.assertEqual((summary['cost'], summary['tokens'], summary['cost_title']), ('1.24', '100', '今日消费'))
        data['daily_spending'][0]['state'] = 'stale'
        self.assertEqual(bubble_summary(data, 'p')['cost_label'], '1.24（旧）')
        data['daily_spending'][0]['day'] = '2026-10-11'
        self.assertEqual(bubble_summary(data, 'p')['cost'], '—')

    def test_replacing_credential_invalidates_daily_account_cache(self):
        import json
        from credentials import atomic_json, save_secret
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            folder = Path(temp)
            path = folder / 'daily-spending.local.json'
            atomic_json(path, {'p': {'amount': '2'}, 'other': {'amount': '3'}})
            save_secret(folder, 'p', 'test-only-fake-credential')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'other': {'amount': '3'}})

    def test_currency_conversion_and_unknown_rules_preserve_raw_quota(self):
        from spending import aggregate_today
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        rows = [{'created_at': int(now.timestamp()), 'quota': '500000'}]
        cny = aggregate_today(rows, {'quota_per_unit': 500000, 'quota_display_type': 'CNY',
                                    'usd_exchange_rate': '7.3'}, now)
        self.assertEqual((cny['amount'], cny['unit']), ('7.3', 'CNY'))
        raw = aggregate_today(rows, {}, now)
        self.assertEqual((raw['amount'], raw['unit']), ('500000', '原始额度'))


if __name__ == '__main__':
    unittest.main()

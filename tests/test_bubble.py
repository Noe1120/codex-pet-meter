"""Bubble behavior: unknown values, screen placement, and owned lifecycle."""
import importlib.util
import json
import sys
import tempfile
import unittest
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/codex-usage-pet/skills/codex-usage/scripts'
sys.path.insert(0, str(SCRIPTS))
WORK = ROOT / '.research/test-work'
WORK.mkdir(parents=True, exist_ok=True)


class BubbleTests(unittest.TestCase):
    def model(self):
        self.assertIsNotNone(importlib.util.find_spec('bubble_model'), 'bubble model is not implemented')
        import bubble_model
        return bubble_model

    def control(self):
        self.assertIsNotNone(importlib.util.find_spec('bubble_control'), 'bubble lifecycle is not implemented')
        import bubble_control
        return bubble_control

    def test_known_zero_balance_and_cost_show_two_decimals(self):
        data = {'today': '2026-10-12', 'balances': [{'id': 'a', 'name': '我的平台', 'amount': '0', 'state': 'success'}],
                'records': [{'day': '2026-10-12', 'platform_id': 'a', 'total_tokens': 1500000, 'cost': {'amount': '1.239', 'unit': 'USD'}}]}
        result = self.model().bubble_summary(data, 'a')
        self.assertEqual(result['balance'], '0.00')
        self.assertEqual(result['cost'], '1.24')
        self.assertEqual(result['tokens'], '1.5M')

    def test_missing_fee_is_unknown_not_partial_or_zero(self):
        data = {'today': '2026-10-12', 'balances': [], 'records': [
            {'day': '2026-10-12', 'platform_id': 'a', 'total_tokens': 10, 'cost': {'amount': '1', 'unit': 'USD'}},
            {'day': '2026-10-12', 'platform_id': 'a', 'total_tokens': 20, 'cost': None}]}
        result = self.model().bubble_summary(data, 'a')
        self.assertEqual(result['balance'], '—')
        self.assertEqual(result['cost'], '—')

    def test_platform_filter_does_not_mix_currencies_or_other_day(self):
        data = {'today': '2026-10-12', 'balances': [], 'records': [
            {'day': '2026-10-12', 'platform_id': 'a', 'total_tokens': 100, 'cost': {'amount': '1', 'unit': 'USD'}},
            {'day': '2026-10-12', 'platform_id': 'b', 'total_tokens': 200, 'cost': {'amount': '2', 'unit': 'CNY'}},
            {'day': '2026-10-11', 'platform_id': 'a', 'total_tokens': 400, 'cost': {'amount': '4', 'unit': 'USD'}}]}
        result = self.model().bubble_summary(data, 'a')
        self.assertEqual((result['cost'], result['tokens']), ('1.00', '300'))

    def test_today_codex_tokens_do_not_require_balance_platform_mapping(self):
        data = {'today': '2026-10-12', 'balances': [{'id': 'a', 'name': 'A'}], 'records': [
            {'day': '2026-10-12', 'platform_id': None, 'total_tokens': 1500000, 'cost': None},
            {'day': '2026-10-11', 'platform_id': None, 'total_tokens': 900, 'cost': None}]}
        result = self.model().bubble_summary(data, 'a')
        self.assertEqual(result['tokens'], '1.5M')
        self.assertEqual(result['cost'], '—')
        self.assertEqual(result['cost_label'], '未配置价格')

    def test_balance_timestamp_is_converted_to_configured_timezone(self):
        model = self.model()
        self.assertEqual(model.balance_time('2026-10-12T12:43:33+00:00', '+08:00'), '20:43:33')
        self.assertEqual(model.balance_time('2026-10-12T12:43:33+00:00', '-05:00'), '07:43:33')
        self.assertEqual(model.balance_time('not a time', '+08:00'), '')
        self.assertEqual(model.balance_time('2026-10-12T12:43:33', '+08:00'), '')

    def test_stale_balance_has_explicit_status(self):
        data = {'today': '2026-10-12', 'records': [], 'balances': [{'id': 'a', 'name': 'A', 'amount': '5', 'state': 'stale', 'message': '认证失败'}]}
        result = self.model().bubble_summary(data, 'a')
        self.assertEqual(result['balance'], '5.00')
        self.assertTrue(result['stale'])

    def test_no_platform_no_calls_is_not_configured_zero_fee(self):
        result = self.model().bubble_summary({'today': '2026-10-12', 'balances': [], 'records': []}, None)
        self.assertEqual(result['cost'], '—')

    def test_selected_platform_before_configuration_does_not_show_zero_cost(self):
        result = self.model().bubble_summary({'today': '2026-10-12', 'balances': [], 'records': []}, 'a')
        self.assertEqual(result['cost'], '—')

    def test_platform_api_error_is_visible_even_without_previous_balance(self):
        result = self.model().bubble_summary({'today': '2026-10-12', 'balances': [
            {'id': 'a', 'name': 'A', 'state': 'error', 'message': '认证失败'}], 'records': []}, 'a')
        self.assertEqual(result['status'], '查询异常')

    def test_unconfigured_balance_is_explicit(self):
        result = self.model().bubble_summary({'today': '2026-10-12', 'records': [], 'balances': [
            {'id': 'a', 'name': 'A', 'state': 'unconfigured'}]}, 'a')
        self.assertEqual(result['status'], '接口未配置')

    def test_yesterday_snapshot_does_not_appear_as_today(self):
        data = {'today': '2026-10-11', 'records': [
            {'day': '2026-10-11', 'platform_id': 'a', 'total_tokens': 10,
             'cost': {'amount': '1', 'unit': 'USD'}}], 'balances': []}
        result = self.model().bubble_summary(data, 'a', today='2026-10-12')
        self.assertEqual((result['tokens'], result['cost']), ('—', '—'))

    def test_external_bubble_preferences_apply_without_restart(self):
        from desktop_bubble import Bubble
        bubble = Bubble.__new__(Bubble)
        bubble.settings = {'platform_id': 'a', 'manual': False}
        bubble.manual, bubble.selected, bubble.position = False, 'a', [100, 100]
        config = {'bubble': {'platform_id': 'b', 'manual': True, 'position': [300, 400]}}
        bubble.apply_config(config)
        self.assertEqual((bubble.selected, bubble.manual, bubble.position), ('b', True, [300, 400]))
        bubble.position = [500, 500]
        bubble.apply_config(config)
        self.assertEqual(bubble.position, [500, 500], 'unchanged settings must not reset live drag position')

    def test_invalid_window_preferences_are_rejected_before_launch(self):
        from main import load_config
        invalid = [{'manual': 'false'}, {'position': [1]}, {'position': [True, 1]},
                   {'position': [float('inf'), 1]}, {'refresh_seconds': 1},
                   {'platform_id': 'missing'}, []]
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            path = Path(temp) / 'config.json'
            for settings in invalid:
                with self.subTest(settings=settings):
                    path.write_text(json.dumps({'bubble': settings}), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_unexpected_worker_failure_reports_generic_error(self):
        from desktop_bubble import RefreshWorker
        def fetch():
            raise RuntimeError('secret must not enter the output')
        worker = RefreshWorker(fetch)
        worker.start()
        worker.thread.join(2)
        result = worker.drain()
        self.assertEqual(result[0][0], 'error')
        self.assertNotIn('secret', result[0][1])

    def test_placement_stays_above_pet_when_space_exists(self):
        self.assertEqual(self.model().bubble_position((1000, 700, 112, 121), (0, 0, 1920, 1040), (224, 150)), (944, 542))

    def test_negative_monitor_coordinates_and_top_edge_are_clamped(self):
        x, y = self.model().bubble_position((-1900, 5, 112, 121), (-1920, 0, 0, 1040), (224, 150))
        self.assertGreaterEqual(x, -1920)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 224, 0)
        self.assertGreater(y, 100)

    def test_saved_pet_anchor_maps_logical_to_physical_monitor(self):
        anchor = self.model().saved_anchor({'x': 1000, 'y': 500, 'displayBounds': {'x': 0, 'y': 0, 'width': 1280, 'height': 720}}, (0, 0, 1920, 1080))
        self.assertEqual(anchor, (1500, 750, 168, 182))

    def test_saved_anchor_missing_or_nonfinite_is_rejected(self):
        self.assertIsNone(self.model().saved_anchor({}, (0, 0, 1920, 1080)))
        self.assertIsNone(self.model().saved_anchor({'x': float('nan'), 'y': 2, 'displayBounds': {'width': 100, 'height': 100}}, (0, 0, 100, 100)))

    def test_main_window_is_excluded_and_ambiguous_overlay_not_chosen(self):
        model = self.model()
        main = {'hwnd': 1, 'path': 'C:/Program Files/WindowsApps/OpenAI.Codex_1/app/ChatGPT.exe', 'visible': True, 'class': 'Chrome_WidgetWin_1', 'style': 0x14c70000, 'exstyle': 0x240100, 'rect': (0, 0, 1000, 800)}
        pet = {**main, 'hwnd': 2, 'style': 0x14000000, 'exstyle': 0x2800a8}
        self.assertIsNone(model.choose_overlay([main]))
        self.assertEqual(model.choose_overlay([main, pet])['hwnd'], 2)
        self.assertIsNone(model.choose_overlay([pet, {**pet, 'hwnd': 3}]))
        self.assertIsNone(model.choose_overlay([{**pet, 'path': 'C:/OtherApp/ChatGPT.exe'}]))

    def test_launch_prefers_pythonw_and_paths_are_independent_arguments(self):
        command = self.control().launch_command(Path('C:/Python/python.exe'), Path('D:/a b/desktop_bubble.py'), Path('D:/a b/config.json'), Path('C:/User/.codex'), demo=True)
        self.assertEqual(command[0], 'C:\\Python\\pythonw.exe')
        self.assertIn('D:\\a b\\config.json', command)
        self.assertIn('--demo', command)

    def test_owned_instance_lock_rejects_second_process(self):
        control = self.control()
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            path = Path(temp) / 'instance.lock'
            first = control.InstanceLock(path)
            second = control.InstanceLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stop_signal_is_bound_to_current_instance(self):
        control = self.control()
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            path = Path(temp) / 'stop.json'
            path.write_text(json.dumps({'instance': 'old'}))
            self.assertFalse(control.should_stop(path, 'new'))
            path.write_text(json.dumps({'instance': 'new'}))
            self.assertTrue(control.should_stop(path, 'new'))

    def test_refresh_worker_never_overlaps_and_returns_result(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop_bubble'))
        from desktop_bubble import RefreshWorker
        entered, finish = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            finish.wait(2)
            return {'amount': '0'}
        worker = RefreshWorker(fetch)
        self.assertTrue(worker.start())
        self.assertTrue(entered.wait(1))
        self.assertFalse(worker.start())
        finish.set()
        worker.thread.join(2)
        self.assertEqual(worker.drain(), [('data', {'amount': '0'})])

    def test_pet_click_reveals_for_five_seconds_and_reclick_resets_timer(self):
        gate = self.model().PetClickGate()
        self.assertFalse(gate.visible(0))
        gate.observe('down', (100, 100), True, 1)
        self.assertFalse(gate.visible(1))
        self.assertTrue(gate.observe('up', (100, 100), True, 1.1))
        self.assertTrue(gate.visible(6.09))
        self.assertFalse(gate.visible(6.1))
        gate.observe('down', (100, 100), True, 7)
        gate.observe('up', (100, 100), True, 7.1)
        gate.observe('down', (100, 100), True, 10)
        gate.observe('up', (100, 100), True, 10.1)
        self.assertTrue(gate.visible(12.2))
        self.assertFalse(gate.visible(15.1))

    def test_drag_and_outside_click_do_not_reveal(self):
        gate = self.model().PetClickGate()
        gate.observe('down', (100, 100), True, 1)
        gate.observe('move', (140, 100), True, 1.1)
        gate.observe('up', (100, 100), True, 1.2)
        self.assertFalse(gate.visible(1.2), 'dragging out and back is still a drag')
        gate.observe('down', (100, 100), False, 2)
        gate.observe('up', (100, 100), True, 2.1)
        self.assertFalse(gate.visible(2.1))
        gate.observe('down', (100, 100), True, 3)
        gate.observe('up', (100, 100), False, 3.1)
        self.assertFalse(gate.visible(3.1))

    def test_holding_or_hidden_pet_cancels_click(self):
        gate = self.model().PetClickGate()
        gate.observe('down', (100, 100), True, 1)
        gate.observe('up', (100, 100), True, 3)
        self.assertFalse(gate.visible(3))
        gate.observe('down', (100, 100), True, 4)
        gate.cancel()
        gate.observe('up', (100, 100), True, 4.1)
        self.assertFalse(gate.visible(4.1))

    def test_invalid_click_display_mode_is_rejected(self):
        from main import load_config
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps({'bubble': {'display': 'bad'}}), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == '__main__':
    unittest.main()

import copy
import io
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capability', ROOT / 'scripts/ingest_sandbox_capability.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Store:
    class exceptions:
        class NoSuchKey(Exception):
            pass
    def __init__(self):
        self.data = {}; self.fail_put = None
    def put_object(self, Bucket, Key, Body, **kwargs):
        if Key == self.fail_put:
            self.fail_put = None
            raise RuntimeError('injected durable-write failure')
        self.data[Key] = bytes(Body)
    def get_object(self, Bucket, Key):
        if Key not in self.data:
            raise self.exceptions.NoSuchKey()
        return {'Body': io.BytesIO(self.data[Key])}
    def get_paginator(self, name):
        return self
    def paginate(self, Bucket, Prefix):
        yield {'Contents': [{'Key': k, 'Size': len(v)} for k, v in list(self.data.items()) if k.startswith(Prefix)]}
    def delete_objects(self, Bucket, Delete):
        for entry in Delete['Objects']:
            self.data.pop(entry['Key'], None)


class CapabilityTest(unittest.TestCase):
    def row(self, suffix='s_1_2_1_c152_m_d_a', count=1):
        return {'p': 'sc1_' + suffix, 'v': count, 'd': 0}

    def test_all_outcomes_and_platforms(self):
        for browser in 'cefso':
            for system in 'wmlaio':
                for device in 'ptd':
                    for outcome in 'nzau':
                        r = self.row(f's_1_2_1_{browser}152_{system}_{device}_{outcome}')
                        self.assertLessEqual(len(r['p']), 40)
                        self.assertIsNotNone(module.decode(r))

    def test_distinct_session_and_event_denominators(self):
        rows = [module.decode(self.row()), module.decode(self.row('e_1_2_1_c152_m_d_r', 4))]
        s = {}; module.fold(s, rows, '2026-09-17', '123')
        self.assertEqual(s['days']['2026-09-17']['sessions'], 1)
        self.assertEqual(s['days']['2026-09-17']['events'], {'radar_ready': 4})
        self.assertEqual(s['days']['2026-09-17']['outcomes'], {'adapter_available': 1})

    def test_retried_workflow_is_idempotent(self):
        s = {}; rows = [module.decode(self.row())]
        self.assertTrue(module.fold(s, rows, '2026-09-17', '123'))
        before = copy.deepcopy(s)
        self.assertFalse(module.fold(s, rows, '2026-09-17', '123'))
        self.assertEqual(s, before)

    def test_no_capability_rows_leak_into_models(self):
        original = {'p': 'reflectivity', 'v': 6, 'd': 30}
        models, capabilities, diagnostics = module.split({'rows': [original, self.row(), {'p': 'sc1_bad', 'v': 1}, self.row('t_1_2_1_o0_o_d_u')]})
        self.assertEqual(models, [original])
        self.assertEqual(len(capabilities), 1)
        self.assertEqual(len(diagnostics), 1)

    def test_legacy_models_payload_stays_exact(self):
        rows = [{'p': 'wind', 'v': 5, 'd': 32}, {'p': 'rain', 'v': 9, 'd': 78}]
        self.assertEqual(module.split({'rows': rows}), (rows, [], []))

    def test_malformed_keys_and_counts_fail_closed(self):
        for r in [self.row('s_1_2_1_c152_m_d_r'), self.row('e_1_2_1_c152_m_d_a'), self.row(count=True), self.row(count=-1), self.row(count='1'), self.row('s_1_2_1_c152_m_d_a_extra'), {'p': 'sc1_' + 'x'*100, 'v': 1}]:
            self.assertIsNone(module.decode(r))

    def test_builds_and_unknown_are_explicit(self):
        self.assertEqual(module.decode(self.row())['build'], 'Beta 1.2.1')
        self.assertEqual(module.decode(self.row('s_u_o0_o_d_u'))['build'], 'unknown')
        self.assertEqual(module.decode(self.row('s_1_2_1_c152_m_d_u'))['outcome'], 'unknown')

    def test_untrusted_row_count_is_bounded(self):
        self.assertEqual(module.decode(self.row(count=499))['count'], 1)
        self.assertEqual(module.decode(self.row('e_1_2_1_c152_m_d_e', 10000))['count'], 500)

    def test_retention_preserves_window_not_invented_history(self):
        s = {}; row = module.decode(self.row())
        module.fold(s, [row], '2026-01-01', '1'); module.fold(s, [row], '2026-09-17', '2')
        self.assertEqual(list(s['days']), ['2026-09-17'])
        self.assertEqual(s['firstReceivedDay'], '2026-01-01')
        self.assertEqual(s['processed'], {'2': '2026-09-17'})

    def test_decoded_fields_are_coarse_allowlist_only(self):
        self.assertEqual(set(module.decode(self.row())), {'kind','build','browser','browserMajor','os','device','outcome','count'})

    def inbox(self, store, batch='1', root=module.PREFIX):
        row = module.decode(self.row())
        key = root + 'inbox/2026-09-17/' + batch + '.json'
        store.data[key] = json.dumps({'receivedDay': '2026-09-17', 'rows': [row]}).encode()
        return key

    def test_coalesced_summary_keeps_every_durable_batch(self):
        store = Store(); self.inbox(store, '1'); self.inbox(store, '2')
        store.data['telemetry/inbox/2026-09-17/model.json'] = b'untouched'
        self.assertEqual(module.summarize(store, 'bucket'), 2)
        result = json.loads(store.data[module.PREFIX + 'summary.json'])
        self.assertEqual(result['days']['2026-09-17']['sessions'], 2)
        self.assertNotIn('processed', result)
        self.assertEqual(store.data['telemetry/inbox/2026-09-17/model.json'], b'untouched')

    def test_partial_summary_write_retry_does_not_lose_or_double(self):
        store = Store(); key = self.inbox(store)
        store.fail_put = module.PREFIX + 'summary.json'
        with self.assertRaisesRegex(RuntimeError, 'durable-write'):
            module.summarize(store, 'bucket')
        self.assertIn(key, store.data)
        module.summarize(store, 'bucket')
        self.assertEqual(json.loads(store.data[module.PREFIX + 'summary.json'])['days']['2026-09-17']['sessions'], 1)
        self.assertNotIn(key, store.data)

    def test_diagnostic_summary_never_creates_user_summary(self):
        store = Store(); root = module.PREFIX + 'checks/'
        self.inbox(store, root=root)
        module.summarize(store, 'bucket', root, True)
        self.assertNotIn(module.PREFIX + 'summary.json', store.data)
        self.assertTrue(json.loads(store.data[root + 'summary.json'])['diagnostic'])

    def test_only_summary_jobs_may_coalesce(self):
        workflow = (ROOT / '.github/workflows/telemetry-ingest.yml').read_text()
        self.assertNotIn('\nconcurrency:', workflow)
        self.assertIn('needs: ingest', workflow)
        self.assertIn('group: sandbox-capability-summary', workflow)


if __name__ == '__main__':
    unittest.main()

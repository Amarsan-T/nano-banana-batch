"""First-SIGINT semantics for real CLI dispatch, using synthetic adapters only."""

import signal
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import app
from nano_batch.cancellation import current_cancellation_state
from nano_batch.models import Batch, BatchItem, GenerationSettings
from nano_batch.runner import execute_one, plan_sequential_batch
from nano_batch.state import RunStateStore
from tests.test_app import OfflinePromptAdapter


def first_signal(mode):
    try:
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    except KeyboardInterrupt:
        if mode == 'translated':
            raise RuntimeError('synthetic transport translation')
    # A blocking library may return normally after swallowing the exception.


class SingleOrderCancellationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / 'synthetic-run'
        self.batch = Batch(
            batch_id='sha256:single-order-cancel', source_schema='test',
            items=(BatchItem(order=1, visual_id='visual_1', prompt='synthetic still life'),),
        )

    def invoke(self, command, adapter, *, download=False):
        previous_handler = signal.getsignal(signal.SIGINT)
        argv = ['app.py', command, 'synthetic-batch.json', '--run-dir', str(self.run_dir)]
        if command in {'generate-one', 'download-one'}:
            argv += ['--order', '1']
        if download:
            argv.append('--download')
        with patch('sys.argv', argv), patch.object(
            app, 'load_batch', return_value=self.batch
        ), patch.object(app, '_load_config', return_value={'variants': 1}), patch.object(
            app, '_flow_session_from_args', return_value=adapter
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            result = app.main()
        self.assertIs(signal.getsignal(signal.SIGINT), previous_handler)
        self.assertIsNone(current_cancellation_state())
        return result

    def generation_cancel(self, mode):
        adapter = OfflinePromptAdapter()
        generate = adapter.generate_one

        def interrupted(**kwargs):
            first_signal(mode)
            return generate(**kwargs)

        adapter.generate_one = interrupted
        self.assertEqual(self.invoke('generate-one', adapter, download=True), 130)
        state = RunStateStore(self.run_dir / 'run_state.json').load()
        item = state.items[0]
        self.assertEqual(item.status, 'running')
        self.assertEqual(item.attempt_count, 1)

        self.assertEqual(item.candidates, [])
        self.assertEqual(item.output_files, [])
        self.assertEqual(item.automatic_policy_retry_count, 0)
        self.assertEqual(item.automatic_generic_failure_retry_count, 0)
        self.assertFalse(any(event[0] == 'download' for event in adapter.events))
        self.assertEqual(plan_sequential_batch(
            self.batch, self.run_dir, GenerationSettings(variants=1)
        ).orders[0].generation_action, 'blocked_retry_required')
        before = (self.run_dir / 'run_state.json').read_bytes()
        next_adapter = OfflinePromptAdapter()
        self.assertEqual(self.invoke('generate-one', next_adapter), 2)
        self.assertEqual(next_adapter.events, [])
        self.assertEqual((self.run_dir / 'run_state.json').read_bytes(), before)

    def test_swallowed_generation_interrupt_does_not_complete_or_download(self):
        self.generation_cancel('swallowed')

    def test_translated_generation_interrupt_remains_interrupt_not_failure(self):
        self.generation_cancel('translated')

    def download_cancel(self, mode):
        execute_one(self.batch, self.run_dir, 1, OfflinePromptAdapter(), GenerationSettings(variants=1))
        adapter = OfflinePromptAdapter()
        download = adapter.download_candidate

        def interrupted(**kwargs):
            result = download(**kwargs)  # Actual valid synthetic JPEG in staging.
            first_signal(mode)
            return result

        adapter.download_candidate = interrupted
        self.assertEqual(self.invoke('download-one', adapter), 130)
        item = RunStateStore(self.run_dir / 'run_state.json').load().items[0]
        self.assertEqual(item.status, 'completed')
        self.assertEqual(item.attempt_count, 1)
        self.assertEqual(item.download_attempt_count, 1)
        self.assertEqual(item.candidates[0].download_status, 'running')
        self.assertEqual(item.output_files, [])
        self.assertFalse(any(event[0] == 'generate' for event in adapter.events))
        self.assertTrue(list((self.run_dir / 'assets').glob('.staging-*/v01.download')))
        self.assertFalse(list((self.run_dir / 'assets').glob('*.jpeg')))
        resumed = OfflinePromptAdapter()
        self.assertEqual(self.invoke('download-one', resumed), 0)
        self.assertFalse(any(event[0] == 'generate' for event in resumed.events))

    def test_swallowed_download_interrupt_preserves_staging_and_download_only_resume(self):
        self.download_cancel('swallowed')

    def test_translated_download_interrupt_preserves_staging_and_download_only_resume(self):
        self.download_cancel('translated')

    def test_late_signal_after_durable_generation_completion_still_exits_130(self):
        save = RunStateStore.save

        def save_then_signal(store, state):
            result = save(store, state)
            if state.items[0].status == 'completed':
                first_signal('swallowed')
            return result

        with patch.object(RunStateStore, 'save', new=save_then_signal):
            self.assertEqual(self.invoke('generate-one', OfflinePromptAdapter()), 130)
        item = RunStateStore(self.run_dir / 'run_state.json').load().items[0]
        self.assertEqual(item.status, 'completed')
        self.assertEqual(item.attempt_count, 1)

    def test_late_signal_after_last_batch_order_does_not_report_normal_exit(self):
        save = RunStateStore.save

        def save_then_signal(store, state):
            result = save(store, state)
            if state.items[0].download_status == 'completed':
                first_signal('swallowed')
            return result

        with patch.object(RunStateStore, 'save', new=save_then_signal):
            self.assertEqual(self.invoke('run-batch', OfflinePromptAdapter()), 130)
        item = RunStateStore(self.run_dir / 'run_state.json').load().items[0]
        self.assertEqual(item.status, 'completed')
        self.assertEqual(item.download_status, 'completed')
        self.assertEqual(item.attempt_count, 1)
        before = (self.run_dir / 'run_state.json').read_bytes()
        adapter = OfflinePromptAdapter()
        self.assertEqual(self.invoke('run-batch', adapter), 0)
        self.assertEqual(adapter.events, [])
        self.assertEqual((self.run_dir / 'run_state.json').read_bytes(), before)

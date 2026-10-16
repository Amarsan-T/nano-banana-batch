"""Completed local bytes cannot bypass logical candidate ownership validation."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nano_batch.errors import CANDIDATE_UUID_OWNERSHIP_CONFLICT
from nano_batch.models import Batch, BatchItem, GenerationSettings
from nano_batch.runner import (
    execute_download_one, execute_sequential_batch, plan_download_one,
    plan_repair_order_candidates, plan_sequential_batch,
)
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture
from tests.test_flow_projects import ProjectFlowAdapter


class CompletedCandidateOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / 'synthetic-run'
        self.batch = Batch(
            batch_id='sha256:completed-ownership-audit', source_schema='test',
            items=tuple(BatchItem(order=i, visual_id=f'visual_{i}', prompt=f'synthetic {i}')
                        for i in (1, 2)),
        )
        self.settings = GenerationSettings(variants=1)
        execute_sequential_batch(
            self.batch, self.run_dir, ProjectFlowAdapter(self.run_dir), self.settings,
            manage_flow_project=True,
        )
        self.store = RunStateStore(self.run_dir / 'run_state.json')
        self.state = self.store.load()

    def snapshot(self):
        return {str(p.relative_to(self.run_dir)): p.read_bytes()
                for p in self.run_dir.rglob('*') if p.is_file()}

    def assert_rejected_without_side_effects(self, *, code=None):
        self.store.save(self.state)
        before = self.snapshot()
        adapter = MagicMock(spec=['generate_one', 'download_candidate'])
        for operation in (
            lambda: plan_download_one(self.batch, self.run_dir, 1),
            lambda: plan_sequential_batch(self.batch, self.run_dir, self.settings),
            lambda: execute_download_one(self.batch, self.run_dir, 1, adapter),
            lambda: execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError) as raised:
                    operation()
                if code is not None:
                    self.assertIn(code, str(raised.exception))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(adapter.mock_calls, [])

    def test_duplicate_uuid_across_fully_completed_orders_is_rejected(self):
        self.state.items[1].candidates[0].candidate_id = self.state.items[0].candidates[0].candidate_id
        self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_conflict_with_incomplete_order_is_not_bypassed_by_completed_asset(self):
        other = self.state.items[1]
        other.candidates[0].candidate_id = self.state.items[0].candidates[0].candidate_id
        other.download_status = 'running'
        self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_missing_identity_is_not_replaced_by_hash_or_filename(self):
        candidate = self.state.items[0].candidates[0]
        candidate.candidate_id = None
        candidate.asset_url = None
        self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_non_string_identity_fails_closed(self):
        candidate = self.state.items[0].candidates[0]
        candidate.asset_url = f'https://flow-content.google/image/{candidate.candidate_id}'
        for value in (42, False, 0, [], {}):
            with self.subTest(value=value):
                candidate.candidate_id = value
                self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_duplicate_uuid_case_variants_are_the_same_logical_identity(self):
        self.state.items[0].candidates[0].candidate_id = '00000000-0000-4000-8000-000000000abc'
        self.state.items[1].candidates[0].candidate_id = self.state.items[0].candidates[0].candidate_id.upper()
        self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_duplicate_uuid_within_completed_order_is_rejected(self):
        first = self.state.items[0].candidates[0]
        second = copy.deepcopy(first)
        second.variant = 2
        second.suggested_filename = first.suggested_filename.replace('__v01.', '__v02.')
        second.local_path = str(Path(first.local_path).with_name(second.suggested_filename))
        second.local_relative_path = str(Path('assets') / second.suggested_filename)
        Path(second.local_path).write_bytes(Path(first.local_path).read_bytes())
        self.state.items[0].candidates.append(second)
        self.state.items[0].output_files.append(second.local_path)
        self.assert_rejected_without_side_effects(code=CANDIDATE_UUID_OWNERSHIP_CONFLICT)

    def test_invalid_recorded_asset_identity_fails_closed(self):
        self.state.items[0].candidates[0].asset_url = 42
        self.assert_rejected_without_side_effects()

    def test_explicit_and_legacy_url_identities_must_agree(self):
        self.state.items[0].candidates[0].asset_url = (
            'https://flow-content.google/image/00000000-0000-4000-8000-000000000099'
        )
        self.assert_rejected_without_side_effects()

    def test_duplicate_matching_order_cannot_be_selected_by_first_position(self):
        duplicate = copy.deepcopy(self.state.items[0])
        duplicate.candidates = []
        duplicate.output_files = []
        self.state.items.append(duplicate)
        self.assert_rejected_without_side_effects()

    def test_valid_completed_orders_are_read_only_and_adapter_free(self):
        before = self.snapshot()
        adapter = MagicMock(spec=['generate_one', 'download_candidate'])
        state, outcomes = execute_sequential_batch(self.batch, self.run_dir, adapter, self.settings)
        self.assertEqual(state, self.state)
        self.assertTrue(all(outcome.generation == 'skipped_completed' for outcome in outcomes))
        self.assertEqual(adapter.mock_calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_legacy_url_identity_remains_supported_for_valid_completed_asset(self):
        candidate = self.state.items[0].candidates[0]
        candidate.asset_url = f'https://flow-content.google/image/{candidate.candidate_id}'
        candidate.candidate_id = None
        self.store.save(self.state)
        before = self.snapshot()
        plan = plan_download_one(self.batch, self.run_dir, 1)
        self.assertEqual(plan.candidates[0].action, 'skip_valid')
        self.assertEqual(self.snapshot(), before)

    def test_explicit_repair_cannot_discard_a_pending_receipt(self):
        fixture = make_pending_download_fixture(self.run_dir / 'pending-fixture')
        other = copy.deepcopy(fixture.candidate)
        other.pending_download_commit = None
        fixture.state.items[1].candidates = [other]
        fixture.store.save(fixture.state)
        before = fixture.store.path.read_bytes(), fixture.staging_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'pending'):
            plan_repair_order_candidates(fixture.batch, fixture.run_dir, 1)
        self.assertEqual((fixture.store.path.read_bytes(), fixture.staging_path.read_bytes()), before)

"""Read-only contract tests for the collaboratively implemented ownership validator."""

import copy
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from nano_batch import runner
from nano_batch.models import CandidateRecord, FlowProjectReference
from nano_batch.state import RunStateStore
from pending_download_fixtures import make_pending_download_fixture, pending_download_test_directory


class PendingDownloadOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.validator = getattr(runner, "validate_pending_download_ownership", None)
        self.assertTrue(
            callable(self.validator),
            "validate_pending_download_ownership() must be implemented.",
        )
        temporary = pending_download_test_directory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_pending_download_fixture(Path(temporary.name) / "run")

    def filesystem_snapshot(self):
        return {
            path.relative_to(self.fixture.run_dir).as_posix(): path.read_bytes()
            for path in self.fixture.run_dir.rglob("*") if path.is_file()
        }

    def call_validator(self):
        fixture = self.fixture
        before = copy.deepcopy((fixture.batch, fixture.state))
        files_before = self.filesystem_snapshot()
        with patch.object(RunStateStore, "save", side_effect=AssertionError("validator must not save state")), patch(
            "nano_batch.runner.validate_image_file", side_effect=AssertionError("byte validation belongs to recovery"),
        ), patch("nano_batch.runner.commit_staged_file", side_effect=AssertionError("validator must not install files")):
            try:
                return self.validator(
                    batch=fixture.batch, state=fixture.state,
                    order=1, variant=1, run_dir=fixture.run_dir,
                )
            finally:
                self.assertEqual((fixture.batch, fixture.state), before)
                self.assertEqual(self.filesystem_snapshot(), files_before)

    def assert_mutations_rejected(self, cases):
        original_batch = copy.deepcopy(self.fixture.batch)
        original_state = copy.deepcopy(self.fixture.state)
        for name, mutate in cases:
            with self.subTest(condition=name):
                self.fixture.batch = copy.deepcopy(original_batch)
                self.fixture.state = copy.deepcopy(original_state)
                mutate()
                with self.assertRaises(ValueError):
                    self.call_validator()

    def assert_expected_paths(self):
        self.assertEqual(self.call_validator(), (self.fixture.canonical_path, self.fixture.staging_path))

    def test_correct_binding_returns_both_paths_without_changes(self):
        self.assertEqual(self.fixture.candidate.suggested_filename.rsplit(".", 1)[-1], "png")
        self.assertEqual(self.fixture.pending.image_format, "JPEG")
        self.assert_expected_paths()

    def test_missing_staging_is_allowed_when_canonical_exists(self):
        self.fixture.canonical_path.write_bytes(self.fixture.staging_path.read_bytes())
        self.fixture.staging_path.unlink()  # Disposable synthetic staging fixture only.
        self.assert_expected_paths()

    def test_missing_both_files_does_not_grant_or_deny_logical_ownership(self):
        self.fixture.staging_path.unlink()
        # Recovery must reject unavailable content separately; this function
        # proves only identity and path consistency, not download completion.
        self.assert_expected_paths()

    def test_effective_prompt_edit_preserves_the_original_source_binding(self):
        self.fixture.item.source_prompt = self.fixture.batch.items[0].prompt
        self.fixture.item.prompt = "synthetic edited prompt"
        self.fixture.item.automatic_policy_retry_count = 1
        self.assert_expected_paths()

    def test_batch_project_and_source_identity_must_match(self):
        fixture = self.fixture
        other_project = str(UUID(int=998))
        self.assert_mutations_rejected((
            ("pending batch", lambda: fixture.replace_pending(batch_id="sha256:other")),
            ("state batch", lambda: setattr(fixture.state, "batch_id", "sha256:other")),
            ("source batch", lambda: setattr(fixture, "batch", replace(fixture.batch, batch_id="sha256:other"))),
            ("pending project", lambda: fixture.replace_pending(project_id=other_project)),
            ("state project", lambda: setattr(fixture.state, "flow_project", FlowProjectReference(
                other_project, f"https://flow.google.com/project/{other_project}",
            ))),
            ("project URL", lambda: setattr(fixture.state, "flow_project", replace(
                fixture.state.flow_project, project_url="https://flow.google.com/",
            ))),
            ("project ownership flag", lambda: setattr(fixture.state, "flow_project_required", False)),
            ("project not created for batch", lambda: setattr(fixture.state, "flow_project", replace(
                fixture.state.flow_project, created_for_batch=False,
            ))),
            ("source prompt", lambda: setattr(fixture.item, "prompt", "wrong source")),
            ("source binding", lambda: setattr(fixture.item, "source_prompt", "wrong source")),
        ))

    def test_order_visual_and_variant_binding_must_match(self):
        fixture = self.fixture
        self.assert_mutations_rejected((
            ("pending Order", lambda: fixture.replace_pending(order=2)),
            ("pending visual", lambda: fixture.replace_pending(visual_id="synthetic_neighbor")),
            ("pending variant", lambda: fixture.replace_pending(variant=2)),
            ("state Order", lambda: setattr(fixture.item, "order", 2)),
            ("state visual", lambda: setattr(fixture.item, "visual_id", "synthetic_neighbor")),
            ("state variant", lambda: setattr(fixture.candidate, "variant", 2)),
            ("duplicate Order", lambda: fixture.state.items.append(copy.deepcopy(fixture.item))),
            ("duplicate Order with another visual", lambda: fixture.state.items.append(replace(
                fixture.item, visual_id="synthetic_other_visual",
            ))),
            ("duplicate source Order", lambda: setattr(fixture, "batch", replace(
                fixture.batch, items=(fixture.batch.items[0], *fixture.batch.items),
            ))),
            ("noninteger state variant", lambda: setattr(fixture.candidate, "variant", 1.0)),
        ))

    def test_candidate_uuid_requires_explicit_unique_unconflicted_ownership(self):
        fixture = self.fixture
        other_candidate = str(UUID(int=102))
        self.assert_mutations_rejected((
            ("pending UUID", lambda: fixture.replace_pending(candidate_id=other_candidate)),
            ("state UUID", lambda: setattr(fixture.candidate, "candidate_id", other_candidate)),
            ("URL-only identity", lambda: setattr(fixture.candidate, "candidate_id", None)),
            ("conflicting URL", lambda: setattr(fixture.candidate, "asset_url", f"https://flow-content.google/image/{other_candidate}")),
            ("malformed URL type", lambda: setattr(fixture.candidate, "asset_url", 3)),
            ("another Order owns UUID", lambda: setattr(fixture.state.items[1], "candidates", [copy.deepcopy(fixture.candidate)])),
            ("duplicate variant owner", lambda: fixture.item.candidates.append(copy.deepcopy(fixture.candidate))),
        ))

    def test_generation_and_download_attempts_must_match_exact_integer_counters(self):
        fixture = self.fixture
        self.assert_mutations_rejected((
            ("pending generation attempt", lambda: fixture.replace_pending(generation_attempt_count=3)),
            ("pending download attempt", lambda: fixture.replace_pending(download_attempt_count=4)),
            ("state generation attempt", lambda: setattr(fixture.item, "attempt_count", 3)),
            ("state download attempt", lambda: setattr(fixture.item, "download_attempt_count", 4)),
            ("noninteger generation attempt", lambda: setattr(fixture.item, "attempt_count", 2.0)),
            ("noninteger download attempt", lambda: setattr(fixture.item, "download_attempt_count", 3.0)),
        ))

    def test_missing_or_malformed_pending_evidence_is_rejected(self):
        fixture = self.fixture

        def missing_project():
            fixture.state.flow_project = None
            fixture.state.flow_project_required = False
            fixture.replace_pending(project_id=None)

        self.assert_mutations_rejected((
            ("no pending record", lambda: setattr(fixture.candidate, "pending_download_commit", None)),
            ("legacy lacks project evidence", missing_project),
            ("record lacks project evidence", lambda: fixture.replace_pending(project_id=None)),
            ("required project missing", lambda: setattr(fixture.state, "flow_project", None)),
            ("record version", lambda: fixture.replace_pending(record_version=2)),
            ("run version", lambda: setattr(fixture.state, "version", 5)),
            ("missing digest", lambda: fixture.replace_pending(sha256="")),
            ("invalid byte size", lambda: fixture.replace_pending(byte_size=0)),
        ))

    def test_paths_must_follow_the_canonical_and_staging_layout(self):
        fixture = self.fixture
        self.assert_mutations_rejected((
            ("canonical traversal", lambda: fixture.replace_pending(canonical_relative_path="assets/../escape.jpeg")),
            ("absolute canonical", lambda: fixture.replace_pending(canonical_relative_path=str(fixture.canonical_path))),
            ("drive-relative canonical", lambda: fixture.replace_pending(canonical_relative_path="C:escape.jpeg")),
            ("wrong extension", lambda: fixture.replace_pending(canonical_relative_path=fixture.pending.canonical_relative_path[:-4] + "png")),
            ("wrong Order filename", lambda: fixture.replace_pending(canonical_relative_path="assets/0002__synthetic_neighbor__v01.jpeg")),
            ("backslash serialization", lambda: fixture.replace_pending(canonical_relative_path=fixture.pending.canonical_relative_path.replace("/", "\\"))),
            ("staging traversal", lambda: fixture.replace_pending(staging_relative_path="assets/.staging-synthetic/../../escape.download")),
            ("absolute staging", lambda: fixture.replace_pending(staging_relative_path=str(fixture.staging_path))),
            ("wrong staging variant", lambda: fixture.replace_pending(staging_relative_path="assets/.staging-synthetic/v02.download")),
            ("ordinary directory", lambda: fixture.replace_pending(staging_relative_path="assets/ordinary/v01.download")),
            ("empty staging suffix", lambda: fixture.replace_pending(staging_relative_path="assets/.staging-/v01.download")),
            ("identical paths", lambda: fixture.replace_pending(staging_relative_path=fixture.pending.canonical_relative_path)),
        ))

    def test_resolved_symlink_or_junction_redirection_is_rejected(self):
        fixture = self.fixture
        real_resolve = Path.resolve
        redirects = (
            (fixture.run_dir / "assets", fixture.run_dir.parent / "outside-assets"),
            (fixture.staging_path.parent, fixture.run_dir.parent / "outside-staging"),
            (fixture.canonical_path, fixture.run_dir.parent / "outside.jpeg"),
            (fixture.canonical_path, fixture.canonical_path.with_name("wrong-candidate.jpeg")),
        )
        for source, destination in redirects:
            with self.subTest(redirection=source.name):
                def redirected_resolve(path, *args, **kwargs):
                    resolved = real_resolve(path, *args, **kwargs)
                    try:
                        relative = resolved.relative_to(source)
                    except ValueError:
                        return resolved
                    return destination / relative

                # Model filesystem resolution offline without requiring Windows
                # symlink privileges, real junctions, or external directories.
                with patch.object(Path, "resolve", new=redirected_resolve):
                    with self.assertRaises(ValueError):
                        self.call_validator()

    def test_incomplete_generation_or_conflicting_download_state_is_rejected(self):
        fixture = self.fixture
        self.assert_mutations_rejected((
            ("generation still running", lambda: setattr(fixture.item, "status", "running")),
            ("generation completion missing", lambda: setattr(fixture.item, "completed_at", None)),
            ("generation error code", lambda: setattr(fixture.item, "generation_error_code", "synthetic-conflict")),
            ("Order download failed", lambda: setattr(fixture.item, "download_status", "failed")),
            ("Order download error", lambda: setattr(fixture.item, "download_error", "synthetic-conflict")),
            ("candidate download failed", lambda: setattr(fixture.candidate, "download_status", "failed")),
            ("candidate download error", lambda: setattr(fixture.candidate, "download_error", "synthetic-conflict")),
            ("candidate already completed", lambda: setattr(fixture.candidate, "download_status", "completed")),
            ("conflicting local metadata", lambda: setattr(fixture.candidate, "local_path", str(fixture.canonical_path))),
            ("premature output registration", lambda: setattr(fixture.item, "output_files", [str(fixture.canonical_path)])),
            ("staging registered as output", lambda: setattr(fixture.item, "output_files", [str(fixture.staging_path)])),
        ))

    def test_other_candidates_cannot_claim_the_same_pending_paths(self):
        fixture = self.fixture
        original = fixture.pending
        for path_field in ("canonical_relative_path", "staging_relative_path"):
            with self.subTest(path_field=path_field):
                changes = dict(
                    order=2, visual_id="synthetic_neighbor", candidate_id=str(UUID(int=102)),
                    canonical_relative_path="assets/0002__synthetic_neighbor__v01.jpeg",
                    staging_relative_path="assets/.staging-other/v01.download",
                )
                changes[path_field] = getattr(original, path_field)
                other = replace(original, **changes)
                fixture.state.items[1].candidates = [CandidateRecord(
                    variant=1, suggested_filename="0002__synthetic_neighbor__v01.png",
                    source_kind="rendered-image", candidate_id=other.candidate_id,
                    download_status="running", pending_download_commit=other,
                )]
                with self.assertRaises(ValueError):
                    self.call_validator()

    def test_global_file_claim_checks_are_read_only_for_all_claim_sources(self):
        fixture = self.fixture
        original = copy.deepcopy(fixture.state)
        for claim in ("local_path", "local_relative_path", "output_files", "same_order_variant"):
            with self.subTest(claim=claim):
                fixture.state = copy.deepcopy(original)
                other = CandidateRecord(
                    variant=1, suggested_filename="0002__synthetic_neighbor__v01.jpeg",
                    source_kind="rendered-image", candidate_id=str(UUID(int=201)),
                )
                if claim == "output_files":
                    fixture.state.items[1].output_files = [fixture.pending.canonical_relative_path]
                elif claim == "same_order_variant":
                    other.variant = 2
                    other.local_path = str(fixture.canonical_path)
                    fixture.item.candidates.append(other)
                else:
                    value = str(fixture.canonical_path) if claim == "local_path" else fixture.pending.canonical_relative_path
                    setattr(other, claim, value)
                    fixture.state.items[1].candidates = [other]
                with self.assertRaises(ValueError):
                    self.call_validator()

    def test_unrelated_completed_legacy_candidate_with_only_absolute_path_is_allowed(self):
        fixture = self.fixture
        path = fixture.run_dir / "assets" / "0002__synthetic_neighbor__v01.jpeg"
        fixture.state.items[1].candidates = [CandidateRecord(
            variant=1, suggested_filename=path.name, source_kind="rendered-image",
            candidate_id=str(UUID(int=201)), download_status="completed", local_path=str(path),
            sha256=fixture.pending.sha256, byte_size=fixture.pending.byte_size,
            image_format="JPEG", mime_type="image/jpeg", width=16, height=9,
            downloaded_at="synthetic prior completion",
        )]
        fixture.state.items[1].output_files = [str(path)]
        self.assert_expected_paths()  # The logical validator neither reads nor requires its bytes.


if __name__ == "__main__":
    unittest.main()

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from nano_batch.adapters.flow import FLOW_HOME_URL, FlowAutomationError, FlowSession
from nano_batch.errors import (
    FLOW_PROJECT_CREATE_CONTROL_UNPROVEN,
    FLOW_PROJECT_CREATION_FAILED,
    FLOW_PROJECT_IDENTITY_UNPROVEN,
    FLOW_PROJECT_REOPEN_FAILED,
    FLOW_USAGE_LIMIT_REACHED,
    WAITING_FOR_FLOW_LIMIT_RESET,
    TemporaryGenerationBlock,
)
from nano_batch.models import (
    Batch,
    BatchItem,
    BrowserDownloadResult,
    DiscoveredCandidate,
    FlowProjectReference,
    GenerationResult,
    GenerationSettings,
)
from nano_batch.runner import (
    execute_sequential_batch,
    initialize_run_state,
    plan_sequential_batch,
    prepare_persisted_flow_project,
)
from nano_batch.state import RunStateStore


OLD_ID = "00000000-0000-4000-8000-000000000001"
NEW_ID = "00000000-0000-4000-8000-000000000008"
OTHER_ID = "00000000-0000-4000-8000-000000000006"
OLD_URL = f"https://flow.google.com/project/{OLD_ID}"
NEW_URL = f"https://flow.google.com/project/{NEW_ID}"
NEW_PROJECT = FlowProjectReference(NEW_ID, NEW_URL, True)


class UrlPage:
    def __init__(self, urls):
        self._urls = list(urls)
        self._last_url = self._urls[-1]
        self.wait_for_timeout = MagicMock()
        self.goto = MagicMock(side_effect=self._goto)

    @property
    def url(self):
        if self._urls:
            self._last_url = self._urls.pop(0)
        return self._last_url

    def _goto(self, url, **_kwargs):
        self._urls = [url, url, url, url]
        self._last_url = url

    def set_urls(self, urls):
        self._urls = list(urls)
        self._last_url = self._urls[-1]


def control_snapshot(**overrides):
    value = {
        "tag": "button",
        "visible_text": "add New project",
        "accessible_name": "add New project",
        "region": "project_list",
        "connected": True,
        "visible": True,
        "enabled": True,
        "pointer_events": "auto",
        "width": 40.0,
        "height": 40.0,
    }
    value.update(overrides)
    return value


class ProjectFlowAdapter:
    def __init__(
        self,
        run_dir,
        *,
        generation_error=None,
        candidate_count=1,
        download_error=None,
    ):
        self.run_dir = Path(run_dir)
        self.generation_error = generation_error
        self.candidate_count = candidate_count
        self.download_error = download_error
        self.create_calls = 0
        self.prepare_calls = []
        self.events = []
        self.binding_seen_before_prepare = False
        self.binding_seen_before_generation = []

    def _binding(self):
        state = RunStateStore(self.run_dir / "run_state.json").load()
        return state.flow_project

    def create_flow_project(self):
        self.create_calls += 1
        self.events.append("create")
        return NEW_PROJECT

    def prepare_flow_project(self, *, project, settings):
        self.prepare_calls.append((project, settings))
        self.binding_seen_before_prepare = self._binding() == project
        self.events.append("prepare")

    def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
        self.binding_seen_before_generation.append(self._binding())
        self.events.append(("generate", item.order))
        if self.generation_error is not None:
            raise self.generation_error
        return GenerationResult(
            candidates=tuple(
                DiscoveredCandidate(
                    source_kind="rendered-image",
                    candidate_id=f"project-candidate-{item.order}-{variant}",
                )
                for variant in range(1, self.candidate_count + 1)
            )
        )

    def download_candidate(self, *, candidate, destination):
        self.events.append(("download", candidate.variant))
        if self.download_error is not None:
            raise self.download_error
        Image.new("RGB", (8, 6), (20, 30, 40)).save(destination, format="JPEG")
        return BrowserDownloadResult(
            candidate_id=str(candidate.candidate_id),
            staged_path=str(destination),
            browser_suggested_filename="candidate.jpeg",
            browser_download_guid="offline-project-guid",
            mechanism="offline-test",
        )


class FlowProjectControlTests(unittest.TestCase):
    def test_unique_project_list_new_project_is_selected_and_generic_adds_cannot_match(self):
        session = FlowSession(OLD_URL, Path("profile"))
        page = MagicMock()
        new_project = MagicMock()
        header = MagicMock()
        editor = MagicMock()
        new_project.evaluate.return_value = control_snapshot()
        header.evaluate.return_value = control_snapshot(
            visible_text="add",
            accessible_name="add",
            region="header",
        )
        editor.evaluate.return_value = control_snapshot(
            visible_text="add",
            accessible_name="add",
            region="other",
            width=32,
            height=32,
        )
        page.locator.return_value.all.return_value = [editor, header, new_project]
        session.page = page

        self.assertIs(session._unique_project_creation_control(), new_project)
        page.locator.assert_called_once_with("button")

    def test_coordinates_and_size_do_not_identify_creation_control(self):
        base = control_snapshot(x=544, y=10, width=192, height=128)
        moved = control_snapshot(x=1250, y=80, width=73, height=19)
        other = control_snapshot(region="other", x=544, y=10)

        self.assertTrue(FlowSession._is_proven_project_creation_control(base))
        self.assertTrue(FlowSession._is_proven_project_creation_control(moved))
        self.assertFalse(FlowSession._is_proven_project_creation_control(other))

    def test_zero_or_multiple_new_project_controls_fail_closed(self):
        session = FlowSession(OLD_URL, Path("profile"))
        page = MagicMock()
        page.locator.return_value.all.return_value = []
        session.page = page
        with patch(
            "nano_batch.adapters.flow._PROJECT_IDENTITY_POLL_ATTEMPTS", 2
        ), self.assertRaisesRegex(
            FlowAutomationError, FLOW_PROJECT_CREATE_CONTROL_UNPROVEN
        ):
            session._wait_for_project_creation_control()
        self.assertEqual(page.wait_for_timeout.call_count, 2)

        first = MagicMock()
        second = MagicMock()
        first.evaluate.return_value = control_snapshot()
        second.evaluate.return_value = control_snapshot()
        page.locator.return_value.all.return_value = [first, second]
        with self.assertRaisesRegex(FlowAutomationError, FLOW_PROJECT_CREATE_CONTROL_UNPROVEN):
            session._wait_for_project_creation_control()

    def test_accessible_name_can_prove_new_project_when_visible_text_is_empty(self):
        self.assertTrue(
            FlowSession._is_proven_project_creation_control(
                control_snapshot(visible_text="", accessible_name="add New project")
            )
        )

    def test_disconnected_hidden_disabled_or_non_pointer_new_project_is_rejected(self):
        for changed in (
            {"connected": False},
            {"visible": False},
            {"enabled": False},
            {"pointer_events": "none"},
            {"width": 0},
            {"height": 0},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(
                    FlowSession._is_proven_project_creation_control(
                        control_snapshot(**changed)
                    )
                )

    def _creation_session(self, after_click_urls, *, window_state="normal"):
        session = FlowSession(OLD_URL, Path("profile"))
        page = UrlPage([OLD_URL])
        control = MagicMock()
        control.click.side_effect = lambda **_kwargs: page.set_urls(after_click_urls)
        session.page = page
        stack = (
            patch.object(session, "_open_current_flow_page_for_project_operation"),
            patch.object(
                session,
                "ensure_production_window_usable",
                return_value={
                    "recovery_required": window_state != "maximized",
                    "recovery_success": True,
                    "reason": "startup_nonmaximized",
                },
            ),
            patch.object(session, "_wait_for_project_creation_control", return_value=control),
        )
        return session, page, control, stack

    def test_home_navigation_one_normal_click_and_stable_uuid_prove_creation(self):
        session, page, control, patches = self._creation_session(
            [NEW_URL + "?private=discard#fragment", NEW_URL, NEW_URL]
        )
        with patches[0], patches[1], patches[2]:
            project = session.create_flow_project()

        self.assertEqual(project, NEW_PROJECT)
        self.assertEqual(session.flow_url, NEW_URL)
        page.goto.assert_called_once_with(
            FLOW_HOME_URL, wait_until="domcontentloaded", timeout=15_000
        )
        control.click.assert_called_once_with(timeout=10_000)
        self.assertNotIn("force", control.click.call_args.kwargs)

    def test_creation_normalizes_window_before_navigation_and_click(self):
        session, _page, control, patches = self._creation_session(
            [NEW_URL, NEW_URL, NEW_URL], window_state="minimized"
        )
        with patches[0], patches[1] as normalize, patches[2]:
            session.create_flow_project()
        normalize.assert_called_once_with(
            reason="startup_nonmaximized",
            require_semantic_surface=False,
        )
        _page.goto.assert_called_once()
        control.click.assert_called_once_with(timeout=10_000)

    def test_unchanged_nonproject_and_malformed_transitions_fail_without_second_click(self):
        cases = (
            ("unchanged", [FLOW_HOME_URL], FLOW_PROJECT_CREATION_FAILED),
            ("flow_nonproject", ["https://flow.google.com/library"], FLOW_PROJECT_CREATION_FAILED),
            ("external", ["https://example.test/project/x"], FLOW_PROJECT_CREATION_FAILED),
            (
                "malformed",
                ["https://flow.google.com/project/not-a-uuid"],
                FLOW_PROJECT_IDENTITY_UNPROVEN,
            ),
        )
        for name, urls, code in cases:
            with self.subTest(name=name):
                session, _page, control, patches = self._creation_session(urls)
                with patch(
                    "nano_batch.adapters.flow._PROJECT_IDENTITY_POLL_ATTEMPTS", 3
                ), patches[0], patches[1], patches[2], self.assertRaisesRegex(
                    FlowAutomationError, code
                ):
                    session.create_flow_project()
                control.click.assert_called_once_with(timeout=10_000)

    def test_creation_path_has_no_force_js_click_focus_restore_or_coordinate_input(self):
        source = inspect.getsource(FlowSession.create_flow_project)
        discovery = inspect.getsource(FlowSession._unique_project_creation_control)
        predicate = inspect.getsource(FlowSession._is_proven_project_creation_control)
        self.assertIn("control.click(timeout=10_000)", source)
        self.assertIn("page.goto", source)
        self.assertIn("FLOW_HOME_URL", source)
        self.assertNotIn("force=", source)
        self.assertNotIn("evaluate", source)
        self.assertNotIn("dispatch_event", source)
        self.assertNotIn("bring_to_front", source)
        self.assertNotIn("mouse.", source)
        self.assertNotIn("bounding_box", discovery)
        self.assertNotIn("nth(", discovery)
        self.assertNotIn("first", discovery)
        self.assertNotIn("last", discovery)
        self.assertIn('"add new project"', predicate)
        self.assertIn('"project_list"', predicate)
        self.assertNotIn('== "add"', predicate)
        self.assertNotIn('== "header"', predicate)


class FlowProjectResumeTests(unittest.TestCase):
    def _session(self, page):
        session = FlowSession(OLD_URL, Path("profile"))
        session.page = page
        return session

    def test_correct_saved_project_needs_no_navigation_and_uses_production_readiness(self):
        page = UrlPage([NEW_URL, NEW_URL, NEW_URL, NEW_URL])
        session = self._session(page)
        settings = GenerationSettings(variants=1)
        readiness = MagicMock(name="readiness_inspection")
        with patch.object(
            session, "_open_current_flow_page_for_project_operation"
        ), patch.object(
            session, "ensure_production_window_usable"
        ) as normalize, patch.object(
            session,
            "_wait_for_authenticated_image_editor",
            return_value=readiness,
        ) as wait_ready, patch.object(session, "configure_generation") as configure:
            session.prepare_flow_project(project=NEW_PROJECT, settings=settings)

        page.goto.assert_not_called()
        wait_ready.assert_called_once_with(NEW_PROJECT)
        configure.assert_called_once_with(settings, inspection=readiness)
        normalize.assert_called_once_with(
            reason="startup_nonmaximized",
            require_semantic_surface=False,
        )
        self.assertEqual(session.flow_url, NEW_URL)

    def test_wrong_project_navigates_to_exact_saved_url_and_verifies_identity(self):
        other_url = f"https://flow.google.com/project/{OTHER_ID}"
        page = UrlPage([other_url])
        session = self._session(page)
        with patch.object(
            session, "_open_current_flow_page_for_project_operation"
        ), patch.object(
            session, "ensure_production_window_usable"
        ), patch.object(session, "configure_generation") as configure:
            session.prepare_flow_project(project=NEW_PROJECT, settings=None)

        page.goto.assert_called_once_with(
            NEW_URL, wait_until="domcontentloaded", timeout=15_000
        )
        configure.assert_not_called()
        self.assertEqual(session.flow_url, NEW_URL)

    def test_saved_project_is_verified_before_window_normalization_and_settings(self):
        other_url = f"https://flow.google.com/project/{OTHER_ID}"
        page = UrlPage([other_url])
        session = self._session(page)
        events = []
        page.goto.side_effect = lambda *_args, **_kwargs: events.append("navigate")
        with patch.object(
            session, "_open_current_flow_page_for_project_operation"
        ), patch.object(
            session,
            "_wait_for_saved_project_reference",
            side_effect=lambda _project: events.append("verify"),
        ), patch.object(
            session,
            "ensure_production_window_usable",
            side_effect=lambda **_kwargs: events.append("normalize"),
        ), patch.object(
            session,
            "_wait_for_authenticated_image_editor",
            side_effect=lambda _project: (
                events.append("editor_ready") or MagicMock(name="readiness_inspection")
            ),
        ), patch.object(
            session,
            "configure_generation",
            side_effect=lambda _settings, **_kwargs: events.append("settings"),
        ):
            session.prepare_flow_project(
                project=NEW_PROJECT,
                settings=GenerationSettings(variants=1),
            )

        self.assertEqual(
            events,
            ["navigate", "verify", "normalize", "editor_ready", "settings"],
        )
        self.assertEqual(session.flow_url, NEW_URL)

    def test_saved_project_reopen_failure_is_fail_closed(self):
        other_url = f"https://flow.google.com/project/{OTHER_ID}"
        page = UrlPage([other_url])
        page.goto = MagicMock()
        session = self._session(page)
        with patch(
            "nano_batch.adapters.flow._PROJECT_IDENTITY_POLL_ATTEMPTS", 3
        ), patch.object(
            session, "_open_current_flow_page_for_project_operation"
        ), patch.object(
            session, "ensure_production_window_usable"
        ), self.assertRaisesRegex(FlowAutomationError, FLOW_PROJECT_REOPEN_FAILED):
            session.prepare_flow_project(project=NEW_PROJECT, settings=None)


class FlowProjectRunStateTests(unittest.TestCase):
    @staticmethod
    def batch(count=2):
        return Batch(
            batch_id="sha256:project-tests",
            source_schema="test",
            items=tuple(
                BatchItem(order=i, visual_id=f"visual_{i}", prompt=f"prompt {i}")
                for i in range(1, count + 1)
            ),
        )

    def test_new_run_persists_one_project_before_readiness_and_order_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = ProjectFlowAdapter(run_dir)
            state, outcomes = execute_sequential_batch(
                self.batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(adapter.create_calls, 1)
            self.assertTrue(adapter.binding_seen_before_prepare)
            self.assertEqual(adapter.binding_seen_before_generation, [NEW_PROJECT, NEW_PROJECT])
            self.assertEqual(adapter.events[:3], ["create", "prepare", ("generate", 1)])
            self.assertEqual(state.flow_project, NEW_PROJECT)
            self.assertTrue(state.flow_project_required)
            self.assertEqual(state.version, 4)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(adapter.prepare_calls, [(NEW_PROJECT, GenerationSettings(variants=1))])

    def test_resume_and_retry_reuse_saved_project_and_never_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            first = ProjectFlowAdapter(run_dir, generation_error=RuntimeError("ordinary failure"))
            with self.assertRaisesRegex(RuntimeError, "ordinary failure"):
                execute_sequential_batch(
                    self.batch(1),
                    run_dir,
                    first,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            self.assertEqual(first.create_calls, 1)
            self.assertEqual(RunStateStore(run_dir / "run_state.json").load().flow_project, NEW_PROJECT)

            retry = ProjectFlowAdapter(run_dir)
            state, _ = execute_sequential_batch(
                self.batch(1),
                run_dir,
                retry,
                GenerationSettings(variants=1),
                retry_failed_generation=True,
                manage_flow_project=True,
            )
            self.assertEqual(retry.create_calls, 0)
            self.assertEqual(retry.prepare_calls[0][0], NEW_PROJECT)
            self.assertEqual(state.flow_project, NEW_PROJECT)

    def test_interrupted_retry_prepares_saved_project_and_skips_completed_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.batch(2)
            first = ProjectFlowAdapter(run_dir)
            generate = first.generate_one

            def interrupt_order_two(*, item, settings, forbidden_candidate_ids=frozenset()):
                if item.order == 2:
                    first.binding_seen_before_generation.append(first._binding())
                    first.events.append(("generate", item.order))
                    raise KeyboardInterrupt
                return generate(
                    item=item,
                    settings=settings,
                    forbidden_candidate_ids=forbidden_candidate_ids,
                )

            first.generate_one = interrupt_order_two
            with self.assertRaises(KeyboardInterrupt):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    first,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            interrupted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(interrupted.items[0].status, "completed")
            self.assertEqual(interrupted.items[1].status, "running")
            order_one_attempts = interrupted.items[0].attempt_count
            before_readiness_failure = (
                run_dir / "run_state.json"
            ).read_bytes()

            not_ready = ProjectFlowAdapter(run_dir)

            def fail_editor_readiness(*, project, settings):
                not_ready.prepare_calls.append((project, settings))
                raise FlowAutomationError(
                    "Image-mode preflight failed because the authenticated Flow "
                    "editor was not ready."
                )

            not_ready.prepare_flow_project = fail_editor_readiness
            with self.assertRaisesRegex(FlowAutomationError, "editor was not ready"):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    not_ready,
                    GenerationSettings(variants=1),
                    retry_failed_generation=True,
                    manage_flow_project=True,
                )
            self.assertEqual(not_ready.create_calls, 0)
            self.assertEqual(not_ready.prepare_calls[0][0], NEW_PROJECT)
            self.assertNotIn(("generate", 1), not_ready.events)
            self.assertNotIn(("generate", 2), not_ready.events)
            self.assertEqual(
                (run_dir / "run_state.json").read_bytes(),
                before_readiness_failure,
            )

            retry = ProjectFlowAdapter(run_dir)
            completed, _ = execute_sequential_batch(
                batch,
                run_dir,
                retry,
                GenerationSettings(variants=1),
                retry_failed_generation=True,
                manage_flow_project=True,
            )

            self.assertEqual(retry.create_calls, 0)
            self.assertEqual(retry.prepare_calls[0][0], NEW_PROJECT)
            self.assertNotIn(("generate", 1), retry.events)
            self.assertIn(("generate", 2), retry.events)
            self.assertEqual(completed.items[0].attempt_count, order_one_attempts)
            self.assertEqual(completed.flow_project, NEW_PROJECT)

    def test_failed_download_resume_reuses_project_and_never_regenerates_order_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.batch(2)
            first = ProjectFlowAdapter(
                run_dir,
                download_error=RuntimeError("download interrupted"),
            )
            with self.assertRaisesRegex(RuntimeError, "download interrupted"):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    first,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            interrupted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(interrupted.items[0].status, "completed")
            self.assertEqual(interrupted.items[0].attempt_count, 1)

            resume = ProjectFlowAdapter(run_dir)
            completed, _ = execute_sequential_batch(
                batch,
                run_dir,
                resume,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(resume.create_calls, 0)
            self.assertEqual(resume.prepare_calls[0][0], NEW_PROJECT)
            self.assertNotIn(("generate", 1), resume.events)
            self.assertIn(("download", 1), resume.events)
            self.assertIn(("generate", 2), resume.events)
            self.assertEqual(completed.items[0].attempt_count, 1)
            self.assertEqual(completed.flow_project, NEW_PROJECT)

    def test_failures_after_binding_never_rotate_project(self):
        failures = (
            ("unusual_activity", RuntimeError("We noticed some unusual activity"), 1, None),
            (
                "usage_limit",
                TemporaryGenerationBlock(
                    "visible Flow usage limit",
                    code=FLOW_USAGE_LIMIT_REACHED,
                    state_status=WAITING_FOR_FLOW_LIMIT_RESET,
                    attempt_activated=True,
                ),
                1,
                None,
            ),
            ("candidate_ambiguity", None, 0, None),
            ("download_error", None, 1, RuntimeError("download failure")),
        )
        for name, generation_error, candidate_count, download_error in failures:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                adapter = ProjectFlowAdapter(
                    run_dir,
                    generation_error=generation_error,
                    candidate_count=candidate_count,
                    download_error=download_error,
                )
                with self.assertRaises(Exception):
                    execute_sequential_batch(
                        self.batch(1),
                        run_dir,
                        adapter,
                        GenerationSettings(variants=1),
                        manage_flow_project=True,
                    )
                state = RunStateStore(run_dir / "run_state.json").load()
                self.assertEqual(adapter.create_calls, 1)
                self.assertEqual(state.flow_project, NEW_PROJECT)

    def test_ambiguous_first_creation_is_not_repeated_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            initialize_run_state(
                self.batch(1), run_dir, require_flow_project=True
            )
            adapter = ProjectFlowAdapter(run_dir)
            with self.assertRaisesRegex(RuntimeError, FLOW_PROJECT_IDENTITY_UNPROVEN):
                execute_sequential_batch(
                    self.batch(1),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            self.assertEqual(adapter.create_calls, 0)
            self.assertEqual(adapter.prepare_calls, [])

    def test_legacy_pending_and_completed_runs_keep_configured_project_semantics(self):
        for completed in (False, True):
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                batch = self.batch(1)
                state = initialize_run_state(batch, run_dir)
                if completed:
                    seed = ProjectFlowAdapter(run_dir)
                    execute_sequential_batch(
                        batch,
                        run_dir,
                        seed,
                        GenerationSettings(variants=1),
                    )
                adapter = ProjectFlowAdapter(run_dir)
                execute_sequential_batch(
                    batch,
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
                self.assertEqual(adapter.create_calls, 0)
                self.assertEqual(adapter.prepare_calls, [])
                if completed:
                    self.assertNotIn(("generate", 1), adapter.events)

    def test_preview_planning_does_not_create_state_or_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "preview"
            plan = plan_sequential_batch(
                self.batch(1), run_dir, GenerationSettings(variants=1)
            )
            self.assertFalse(plan.state_exists)
            self.assertFalse(run_dir.exists())

    def test_persisted_project_helper_is_noop_for_legacy_and_fails_unbound_managed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "legacy"
            managed_dir = root / "managed"
            batch = self.batch(1)
            initialize_run_state(batch, legacy_dir)
            legacy_adapter = ProjectFlowAdapter(legacy_dir)
            self.assertIsNone(
                prepare_persisted_flow_project(
                    batch, legacy_dir, legacy_adapter, GenerationSettings(variants=1)
                )
            )
            self.assertEqual(legacy_adapter.prepare_calls, [])

            initialize_run_state(batch, managed_dir, require_flow_project=True)
            managed_adapter = ProjectFlowAdapter(managed_dir)
            with self.assertRaisesRegex(RuntimeError, FLOW_PROJECT_IDENTITY_UNPROVEN):
                prepare_persisted_flow_project(
                    batch, managed_dir, managed_adapter, GenerationSettings(variants=1)
                )
            self.assertEqual(managed_adapter.create_calls, 0)

    def test_project_metadata_round_trip_is_exact_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            state = initialize_run_state(
                self.batch(1), run_dir, require_flow_project=True
            )
            state.flow_project = NEW_PROJECT
            store = RunStateStore(run_dir / "run_state.json")
            store.save(state)
            first = store.path.read_bytes()
            store.save(store.load())
            second = store.path.read_bytes()
            raw = json.loads(second)

            self.assertEqual(first, second)
            self.assertEqual(
                raw["flow_project"],
                {
                    "created_for_batch": True,
                    "project_id": NEW_ID,
                    "project_url": NEW_URL,
                },
            )
            self.assertNotIn("?", raw["flow_project"]["project_url"])
            self.assertNotIn("#", raw["flow_project"]["project_url"])

    def test_persisted_project_identity_cannot_be_replaced_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            state = initialize_run_state(
                self.batch(1), run_dir, require_flow_project=True
            )
            state.flow_project = NEW_PROJECT
            store = RunStateStore(run_dir / "run_state.json")
            store.save(state)

            state.flow_project = FlowProjectReference(
                OTHER_ID,
                f"https://flow.google.com/project/{OTHER_ID}",
                True,
            )
            with self.assertRaisesRegex(ValueError, "cannot be replaced"):
                store.save(state)

            state.flow_project = None
            with self.assertRaisesRegex(ValueError, "cannot be replaced"):
                store.save(state)
            self.assertEqual(store.load().flow_project, NEW_PROJECT)

    def test_raw_legacy_state_without_project_fields_loads_without_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_state.json"
            legacy = {
                "schema": "nano-banana-batch-run-state",
                "version": 3,
                "batch_id": "sha256:legacy",
                "items": [
                    {
                        "order": 1,
                        "visual_id": "legacy",
                        "prompt": "legacy prompt",
                    }
                ],
                "created_at": "old",
                "updated_at": "old",
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            before = path.read_bytes()

            state = RunStateStore(path).load()

            self.assertIsNone(state.flow_project)
            self.assertFalse(state.flow_project_required)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

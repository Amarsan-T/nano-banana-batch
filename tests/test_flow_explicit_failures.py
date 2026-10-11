import inspect
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image

from nano_batch.adapters.flow import (
    FlowAutomationError,
    FlowGenericGenerationFailed,
    FlowPolicyRejected,
    FlowSession,
    FlowUnusualActivity,
    FlowUsageLimitReached,
)
from nano_batch.errors import (
    FLOW_GENERIC_GENERATION_FAILED,
    FLOW_POLICY_REJECTED,
    FLOW_UNUSUAL_ACTIVITY,
    WAITING_FOR_FLOW_LIMIT_RESET,
)
from nano_batch.models import (
    Batch,
    BatchItem,
    BrowserDownloadResult,
    CandidateRecord,
    DiscoveredCandidate,
    FlowProjectReference,
    GenerationResult,
    GenerationSettings,
)
from nano_batch.runner import (
    execute_sequential_batch,
    initialize_run_state,
    plan_sequential_batch,
)
from nano_batch.state import RunStateStore


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"
POLICY_FAILURE = (
    "Failed. This generation might violate our policies. "
    "Please try a different prompt or send feedback. "
    "You have not been charged for this generation."
)
UNUSUAL_ACTIVITY_FAILURE = (
    "Failed. We noticed some unusual activity. "
    "Please visit the Help Center for more information. "
    "You have not been charged for this generation."
)
UNUSUAL_ACTIVITY_EXTENSION_FAILURE = (
    "Failed. We noticed some unusual activity. "
    "Note that browser extensions that modify the page may cause issues. "
    "Please visit the Help Center for more information. "
    "You have not been charged for this generation."
)
GENERIC_GENERATION_FAILURE = (
    "Failed. Sorry, this image failed to generate. "
    "You have not been charged for this generation."
)
HIGH_DEMAND_FAILURE = (
    "Failed. Flow is experiencing high demand. Please try again later. "
    "You have not been charged for this generation."
)
PROJECT = FlowProjectReference(
    project_id="00000000-0000-4000-8000-000000000008",
    project_url="https://flow.google.com/project/00000000-0000-4000-8000-000000000008",
    created_for_batch=True,
)


def _generation_failure_observer_scripts():
    session = FlowSession(PROJECT_URL, Path("profile"))
    page = MagicMock()
    session.page = page
    session._begin_current_generation_attempt_observation()
    begin_script = page.evaluate.call_args.args[0]
    session._arm_current_generation_attempt_observation()
    arm_script = page.evaluate.call_args.args[0]
    page.evaluate.return_value = {"failures": []}
    session._current_generation_attempt_failures()
    poll_script = page.evaluate.call_args.args[0]
    return begin_script, arm_script, poll_script


def _run_generation_failure_observer_scenario(scenario: str):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for embedded observer regression tests.")
    begin_script, arm_script, poll_script = _generation_failure_observer_scripts()
    harness = f"""
const beginScript = {json.dumps(begin_script)};
const armScript = {json.dumps(arm_script)};
const pollScript = {json.dumps(poll_script)};
const scenario = {json.dumps(scenario)};
const unusualExact = {json.dumps(UNUSUAL_ACTIVITY_FAILURE)};
const unusualExtensionExact = {json.dumps(UNUSUAL_ACTIVITY_EXTENSION_FAILURE)};
const genericExact = {json.dumps(GENERIC_GENERATION_FAILURE)};
const highDemandExact = {json.dumps(HIGH_DEMAND_FAILURE)};
const highDemandScenario = scenario.startsWith('high_');
const genericScenario = scenario.startsWith('generic_');
const extensionScenario = scenario.startsWith('extension_');
const mode = highDemandScenario ? scenario.slice(5) : genericScenario ? scenario.slice(8)
  : (extensionScenario ? scenario.slice(10) : scenario);
const exact = highDemandScenario ? highDemandExact : genericScenario ? genericExact
  : (extensionScenario ? unusualExtensionExact : unusualExact);

class Element {{
  constructor(tag, parent = null) {{
    this.tagName = tag.toUpperCase();
    this.parentElement = parent;
    this.children = [];
    if (parent) parent.children.push(this);
    this.isConnected = true;
    this.innerText = '';
    this.textContent = '';
    this.attributes = {{}};
  }}
  getAttribute(name) {{
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name] : null;
  }}
  getBoundingClientRect() {{
    return {{x: 0, y: 0, width: 200, height: 80}};
  }}
  matches(_selector) {{ return false; }}
  querySelectorAll(_selector) {{ return []; }}
}}
class MutationObserver {{
  constructor(callback) {{
    this.callback = callback;
    globalThis.lastObserver = this;
  }}
  observe(_target, options) {{ this.options = options; }}
  disconnect() {{}}
  takeRecords() {{ return []; }}
}}
globalThis.Element = Element;
globalThis.MutationObserver = MutationObserver;
globalThis.getComputedStyle = (_element) => ({{
  visibility: 'visible', display: 'block'
}});
let now = 100;
globalThis.performance = {{now: () => ++now}};
globalThis.window = {{}};
const body = new Element('body');
globalThis.document = {{body, createTreeWalker(root) {{
  const nodes = [];
  const visit = (element) => {{
    for (const child of element.children) {{ nodes.push(child); visit(child); }}
  }};
  visit(root);
  return {{nextNode: () => nodes.shift() || null}};
}}}};

const makeDeepCard = () => {{
  const card = new Element('section', body);
  card.attributes['aria-label'] = 'Failure card';
  let leaf = card;
  for (let index = 0; index < 8; index++) {{
    leaf = new Element('div', leaf);
  }}
  return {{card, leaf}};
}};

let historical = null;
let changed = null;
if (mode === 'historical') {{
  historical = makeDeepCard();
  historical.card.innerText = exact;
  historical.card.textContent = exact;
}} else if (mode === 'reused' || mode === 'partial') {{
  changed = makeDeepCard();
}}

(0, eval)('(' + beginScript + ')')();
(0, eval)('(' + armScript + ')')();

if (mode === 'reused' || mode === 'partial') {{
  changed.card.innerText = mode === 'partial'
    ? (highDemandScenario ? 'Flow is experiencing high demand. Please try again later.' : 'Sorry, this image failed to generate.')
    : exact.replace(/\. /g, '.\\n');
  changed.card.textContent = changed.card.innerText;
  const textNode = {{parentElement: changed.card, textContent: changed.card.textContent}};
  const records = Array.from({{length: 737}}, () => ({{
    type: 'characterData', target: textNode, oldValue: ''
  }}));
  lastObserver.callback(records);
}} else if (mode === 'added' || mode === 'added_nested_anchor') {{
  changed = makeDeepCard();
  changed.card.innerText = exact;
  changed.card.textContent = exact;
  if (mode === 'added_nested_anchor') {{
    const helpCenterAnchor = new Element('a', changed.card);
    helpCenterAnchor.innerText = 'Help Center';
    helpCenterAnchor.textContent = 'Help Center';
  }}
  lastObserver.callback([{{
    type: 'childList', target: body, addedNodes: [changed.card]
  }}]);
}}

const snapshot = (0, eval)('(' + pollScript + ')')();
process.stdout.write(JSON.stringify(snapshot));
"""
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "observer_regression.js"
        script_path.write_text(harness, encoding="utf-8")
        completed = subprocess.run(
            [node, str(script_path)],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


class ExplicitPolicyFailureDetectorTests(unittest.TestCase):
    def test_exact_full_policy_card_is_detected_with_distinct_code(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = [POLICY_FAILURE]
        session.page = page

        self.assertEqual(
            session._current_generation_attempt_failures(),
            (POLICY_FAILURE,),
        )
        with self.assertRaisesRegex(
            FlowAutomationError,
            "FLOW_POLICY_REJECTED:.*might violate its policies.*not charged",
        ):
            session._raise_for_current_generation_attempt_failure()

    def test_result_wait_checks_window_then_exits_before_timeout_or_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session._page_cdp_session = object()
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(POLICY_FAILURE,),
        ), patch.object(
            session, "discover_candidates"
        ) as discover, patch.object(
            session, "_generation_window_watchdog", return_value=None
        ) as watchdog, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ), self.assertRaisesRegex(
            FlowAutomationError, FLOW_POLICY_REJECTED
        ):
            session.wait_for_result(set(), 300, expected_count=1)

        discover.assert_not_called()
        watchdog.assert_called_once_with(recovery_count=0)
        session.page.wait_for_timeout.assert_not_called()

    def test_historical_pre_start_policy_card_is_not_enumerated_or_retained(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page

        session._begin_current_generation_attempt_observation()
        begin_script = page.evaluate.call_args.args[0]
        session._arm_current_generation_attempt_observation()
        arm_script = page.evaluate.call_args.args[0]

        self.assertIn("active: false", begin_script)
        self.assertIn("if (!state.active) return", begin_script)
        self.assertNotIn("document.querySelectorAll", begin_script)
        self.assertIn("state.failures = []", arm_script)

    def test_historical_pre_start_unusual_card_is_ignored_by_same_observer_gate(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        session.page = page

        session._begin_current_generation_attempt_observation()
        begin_script = page.evaluate.call_args.args[0]
        session._arm_current_generation_attempt_observation()
        arm_script = page.evaluate.call_args.args[0]

        self.assertIn("active: false", begin_script)
        self.assertIn("if (!state.active) return", begin_script)
        self.assertIn("please visit the help center for more information", begin_script)
        self.assertNotIn("document.querySelectorAll", begin_script)
        self.assertIn("state.failures = []", arm_script)

    def test_current_attempt_observer_is_armed_immediately_before_start(self):
        source = inspect.getsource(FlowSession.submit_prompt)
        begin = source.index("self._begin_current_generation_attempt_observation()")
        arm = source.index("self._arm_current_generation_attempt_observation()")
        activate = source.index("self._activate_generation_submit_button")

        self.assertLess(begin, arm)
        self.assertLess(arm, activate)

    def test_partial_or_generic_policy_text_is_not_enough(self):
        partials = (
            "This generation might violate our policies.",
            "Failed",
            "You have not been charged for this generation.",
            (
                "Failed. This generation might violate our policies. "
                "You have not been charged for this generation."
            ),
        )
        for text in partials:
            with self.subTest(text=text):
                session = FlowSession(PROJECT_URL, Path("profile"))
                page = MagicMock()
                page.evaluate.return_value = [text]
                session.page = page
                self.assertEqual(session._current_generation_attempt_failures(), ())

    def test_existing_unusual_activity_signature_still_works(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = [UNUSUAL_ACTIVITY_FAILURE]
        session.page = page

        self.assertEqual(
            session._current_generation_attempt_failures(),
            (UNUSUAL_ACTIVITY_FAILURE,),
        )
        with self.assertRaisesRegex(
            FlowUnusualActivity, FLOW_UNUSUAL_ACTIVITY
        ):
            session._raise_for_current_generation_attempt_failure()

    def test_browser_extension_wording_classifies_as_unusual_activity(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = [UNUSUAL_ACTIVITY_EXTENSION_FAILURE]
        session.page = page

        self.assertEqual(
            session._current_generation_attempt_failures(),
            (UNUSUAL_ACTIVITY_FAILURE,),
        )
        with self.assertRaises(FlowUnusualActivity) as raised:
            session._raise_for_current_generation_attempt_failure()
        self.assertEqual(raised.exception.code, FLOW_UNUSUAL_ACTIVITY)

    def test_browser_extension_wording_matches_multiline_observer_text(self):
        snapshot = _run_generation_failure_observer_scenario("extension_reused")

        self.assertEqual(snapshot["failures"], [UNUSUAL_ACTIVITY_FAILURE])
        self.assertEqual(
            snapshot["diagnostics"]["last_failure_detection_reason"],
            "exact_unusual_activity_signature",
        )

    def test_browser_extension_wording_matches_nested_help_center_anchor(self):
        snapshot = _run_generation_failure_observer_scenario(
            "extension_added_nested_anchor"
        )

        self.assertEqual(snapshot["failures"], [UNUSUAL_ACTIVITY_FAILURE])
        self.assertEqual(
            snapshot["diagnostics"]["unusual_signature_match_count"],
            1,
        )

    def test_unusual_activity_exits_result_wait_before_timeout_or_discovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session._page_cdp_session = object()
        with patch.object(
            session,
            "_current_generation_attempt_failures",
            return_value=(UNUSUAL_ACTIVITY_FAILURE,),
        ), patch.object(
            session, "discover_candidates"
        ) as discover, patch.object(
            session, "_generation_window_watchdog", return_value=None
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ), self.assertRaises(FlowUnusualActivity):
            session.wait_for_result(set(), 300, expected_count=1)

        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_order_104_reused_deep_card_is_found_by_first_poll_scan(self):
        snapshot = _run_generation_failure_observer_scenario("reused")
        diagnostics = snapshot["diagnostics"]
        self.assertEqual(snapshot["failures"], [UNUSUAL_ACTIVITY_FAILURE])
        self.assertTrue(diagnostics["failure_observer_active"])
        self.assertEqual(diagnostics["post_start_failure_mutation_count"], 737)
        self.assertGreater(diagnostics["visible_failure_container_count"], 0)
        self.assertGreater(diagnostics["current_attempt_failure_container_count"], 0)
        self.assertEqual(diagnostics["unusual_signature_match_count"], 1)
        self.assertEqual(
            diagnostics["last_failure_detection_reason"],
            "exact_unusual_activity_signature",
        )

        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session.page.evaluate.return_value = snapshot
        with patch.object(
            session, "discover_candidates"
        ) as discover, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ), self.assertRaises(FlowUnusualActivity):
            session.wait_for_result(set(), 300, expected_count=1)

        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_unchanged_historical_unusual_card_is_not_a_current_attempt_container(self):
        snapshot = _run_generation_failure_observer_scenario("historical")
        self.assertEqual(snapshot["failures"], [])
        self.assertEqual(
            snapshot["diagnostics"]["current_attempt_failure_container_count"],
            0,
        )
        self.assertEqual(
            snapshot["diagnostics"]["last_failure_detection_reason"],
            "no_current_attempt_failure_container",
        )

    def test_historical_browser_extension_variant_is_ignored(self):
        snapshot = _run_generation_failure_observer_scenario(
            "extension_historical"
        )

        self.assertEqual(snapshot["failures"], [])
        self.assertEqual(
            snapshot["diagnostics"]["current_attempt_failure_container_count"],
            0,
        )

    def test_new_deep_exact_card_added_after_start_is_detected(self):
        snapshot = _run_generation_failure_observer_scenario("added")
        self.assertEqual(snapshot["failures"], [UNUSUAL_ACTIVITY_FAILURE])
        self.assertEqual(
            snapshot["diagnostics"]["last_failure_detection_reason"],
            "exact_unusual_activity_signature",
        )

    def test_exact_generic_failure_has_distinct_classification(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = [GENERIC_GENERATION_FAILURE]
        session.page = page

        self.assertEqual(
            session._current_generation_attempt_failures(),
            (GENERIC_GENERATION_FAILURE,),
        )
        with self.assertRaisesRegex(
            FlowGenericGenerationFailed,
            FLOW_GENERIC_GENERATION_FAILED,
        ):
            session._raise_for_current_generation_attempt_failure()

    def test_generic_failure_reused_card_exits_first_poll_before_timeout(self):
        snapshot = _run_generation_failure_observer_scenario("generic_reused")
        diagnostics = snapshot["diagnostics"]
        self.assertEqual(snapshot["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertEqual(
            diagnostics["generic_generation_signature_match_count"],
            1,
        )
        self.assertEqual(
            diagnostics["last_failure_detection_reason"],
            "exact_generic_generation_failure_signature",
        )

        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session.page.evaluate.return_value = snapshot
        with patch.object(
            session, "discover_candidates"
        ) as discover, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ), self.assertRaises(FlowGenericGenerationFailed):
            session.wait_for_result(set(), 300, expected_count=1)

        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_historical_generic_failure_card_is_ignored(self):
        snapshot = _run_generation_failure_observer_scenario("generic_historical")
        self.assertEqual(snapshot["failures"], [])
        self.assertEqual(
            snapshot["diagnostics"]["current_attempt_failure_container_count"],
            0,
        )

    def test_new_generic_failure_card_is_detected_after_start(self):
        snapshot = _run_generation_failure_observer_scenario("generic_added")
        self.assertEqual(snapshot["failures"], [GENERIC_GENERATION_FAILURE])

    def test_partial_generic_failure_clause_is_ignored(self):
        snapshot = _run_generation_failure_observer_scenario("generic_partial")
        self.assertEqual(snapshot["failures"], [])
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = ["Sorry, this image failed to generate."]
        session.page = page
        self.assertEqual(session._current_generation_attempt_failures(), ())

    def test_partial_unusual_activity_text_does_not_trigger_recovery(self):
        partials = (
            "Failed. We noticed some unusual activity.",
            "Note that browser extensions that modify the page may cause issues.",
            (
                "Failed. We noticed some unusual activity. "
                "You have not been charged for this generation."
            ),
            "Please visit the Help Center for more information.",
        )
        for text in partials:
            with self.subTest(text=text):
                session = FlowSession(PROJECT_URL, Path("profile"))
                page = MagicMock()
                page.evaluate.return_value = [text]
                session.page = page
                self.assertEqual(session._current_generation_attempt_failures(), ())

    def test_current_browser_extension_variant_exits_before_timeout_or_discovery(self):
        snapshot = _run_generation_failure_observer_scenario("extension_added")
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session._page_cdp_session = object()
        session.page.evaluate.return_value = snapshot

        with patch.object(session, "discover_candidates") as discover, patch.object(
            session, "_generation_window_watchdog", return_value=None
        ), patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0],
        ), self.assertRaises(FlowUnusualActivity) as raised:
            session.wait_for_result(set(), 300, expected_count=1)

        self.assertEqual(raised.exception.code, FLOW_UNUSUAL_ACTIVITY)
        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()

    def test_browser_extension_variant_enters_existing_same_project_recovery(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        session.page.evaluate.return_value = [UNUSUAL_ACTIVITY_EXTENSION_FAILURE]
        successful = GenerationResult(
            candidates=(DiscoveredCandidate("rendered-image", candidate_id="fresh-v01"),)
        )
        attempts = []

        def attempt(**_kwargs):
            attempts.append("attempt")
            if len(attempts) == 1:
                session._raise_for_current_generation_attempt_failure()
            return successful

        with patch.object(session, "open", return_value=session), patch.object(
            session, "close"
        ), patch.object(
            session, "_generate_one_in_open_session", side_effect=attempt
        ), patch.object(
            session,
            "_recover_unusual_activity_in_open_session",
            return_value=object(),
        ) as recover:
            result = session.generate_one_with_unusual_activity_recovery(
                item=BatchItem(order=1, visual_id="visual_1", prompt="same prompt"),
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset(),
                project=PROJECT,
                retry_started=lambda: None,
            )

        self.assertIs(result, successful)
        self.assertEqual(attempts, ["attempt", "attempt"])
        recover.assert_called_once_with(project=PROJECT)

    def test_high_demand_text_is_not_unusual_activity(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = ["Flow is experiencing high demand."]
        session.page = page

        self.assertEqual(session._current_generation_attempt_failures(), ())

    def test_same_session_recovery_navigates_home_then_exact_saved_project(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        browser = object()
        cdp_session = object()
        inspection = object()
        session.page = page
        session._browser = browser
        session._page_cdp_session = cdp_session

        with patch.object(session, "_wait_for_flow_home") as wait_home, patch.object(
            session, "_wait_for_saved_project_reference"
        ) as wait_project, patch.object(
            session, "ensure_production_window_usable"
        ) as ensure_window, patch.object(
            session,
            "_wait_for_authenticated_image_editor",
            return_value=inspection,
        ) as wait_editor, patch.object(session, "open") as open_session, patch.object(
            session, "close"
        ) as close_session:
            result = session._recover_unusual_activity_in_open_session(project=PROJECT)

        self.assertIs(result, inspection)
        self.assertEqual(
            page.goto.call_args_list,
            [
                call("https://flow.google.com/", wait_until="domcontentloaded", timeout=15_000),
                call(PROJECT.project_url, wait_until="domcontentloaded", timeout=15_000),
            ],
        )
        wait_home.assert_called_once_with()
        wait_project.assert_called_once_with(PROJECT)
        ensure_window.assert_called_once_with(
            reason="startup_nonmaximized",
            require_semantic_surface=False,
        )
        wait_editor.assert_called_once_with(PROJECT)
        open_session.assert_not_called()
        close_session.assert_not_called()
        self.assertIs(session.page, page)
        self.assertIs(session._browser, browser)
        self.assertIs(session._page_cdp_session, cdp_session)

    def test_recovery_wrapper_keeps_one_open_session_and_uses_fresh_editor(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        fresh_inspection = object()
        successful = GenerationResult(
            candidates=(DiscoveredCandidate("rendered-image", candidate_id="fresh-v01"),)
        )
        events = []

        def attempt(*, inspection=None, **_kwargs):
            events.append(("attempt", inspection))
            if len(events) == 1:
                raise FlowUnusualActivity()
            return successful

        with patch.object(session, "open", return_value=session) as open_session, patch.object(
            session, "close", side_effect=lambda **_kwargs: events.append(("close", None))
        ) as close_session, patch.object(
            session, "_generate_one_in_open_session", side_effect=attempt
        ), patch.object(
            session,
            "_recover_unusual_activity_in_open_session",
            return_value=fresh_inspection,
        ) as recover:
            result = session.generate_one_with_unusual_activity_recovery(
                item=BatchItem(order=1, visual_id="visual_1", prompt="same prompt"),
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset(),
                project=PROJECT,
                retry_started=lambda: events.append(("retry_started", None)),
            )

        self.assertIs(result, successful)
        self.assertEqual(
            events,
            [
                ("attempt", None),
                ("retry_started", None),
                ("attempt", fresh_inspection),
                ("close", None),
            ],
        )
        open_session.assert_called_once_with()
        close_session.assert_called_once_with(intentional_cancellation=False)
        recover.assert_called_once_with(project=PROJECT)

    def test_generic_retries_keep_same_open_session_without_home_navigation(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        successful = GenerationResult(
            candidates=(DiscoveredCandidate("rendered-image", candidate_id="fresh-v01"),)
        )
        events = []

        def attempt(*, item, inspection=None, **_kwargs):
            events.append(("attempt", item.order, item.prompt, inspection))
            if sum(event[0] == "attempt" for event in events) <= 2:
                raise FlowGenericGenerationFailed()
            return successful

        def generic_retry_started(exc):
            events.append(("generic_retry", exc.code))
            return True

        with patch.object(session, "open", return_value=session) as open_session, patch.object(
            session, "close", side_effect=lambda **_kwargs: events.append(("close",))
        ) as close_session, patch.object(
            session, "_generate_one_in_open_session", side_effect=attempt
        ) as generate, patch.object(
            session, "_recover_unusual_activity_in_open_session"
        ) as recover:
            result = session.generate_one_with_unusual_activity_recovery(
                item=BatchItem(order=7, visual_id="visual_7", prompt="same prompt"),
                settings=GenerationSettings(variants=1),
                forbidden_candidate_ids=frozenset(),
                project=PROJECT,
                retry_started=lambda: self.fail("unusual retry was not expected"),
                generic_failure_retry_started=generic_retry_started,
            )

        self.assertIs(result, successful)
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(
            [event for event in events if event[0] == "attempt"],
            [
                ("attempt", 7, "same prompt", None),
                ("attempt", 7, "same prompt", None),
                ("attempt", 7, "same prompt", None),
            ],
        )
        self.assertEqual(
            [event for event in events if event[0] == "generic_retry"],
            [
                ("generic_retry", FLOW_GENERIC_GENERATION_FAILED),
                ("generic_retry", FLOW_GENERIC_GENERATION_FAILED),
            ],
        )
        open_session.assert_called_once_with()
        close_session.assert_called_once_with(intentional_cancellation=False)
        recover.assert_not_called()

    def test_usage_limit_remains_a_distinct_temporary_block(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = MagicMock()
        page.evaluate.return_value = ["Failed. You have reached your usage limit."]
        session.page = page

        with self.assertRaises(FlowUsageLimitReached):
            session._raise_for_current_generation_attempt_failure()

    def test_successful_result_wait_behavior_is_unchanged(self):
        session = FlowSession(PROJECT_URL, Path("profile"))
        session.page = MagicMock()
        candidate = DiscoveredCandidate("rendered-image", candidate_id="current-v01")
        with patch.object(
            session, "_current_generation_attempt_failures", return_value=()
        ), patch.object(
            session, "_progress_signals", return_value=()
        ), patch.object(
            session, "discover_candidates", return_value=(candidate,)
        ) as discover, patch(
            "nano_batch.adapters.flow.time.monotonic",
            side_effect=[0.0, 0.0, 0.0, 0.0],
        ):
            result = session.wait_for_result(set(), 1, expected_count=1)

        self.assertEqual(result, (candidate,))
        self.assertEqual(discover.call_count, 3)

    def test_observer_contains_exact_signatures_and_only_bounded_causal_scan(self):
        source = inspect.getsource(
            FlowSession._begin_current_generation_attempt_observation
        ).casefold()
        self.assertIn("failed", source)
        self.assertIn("this generation might violate our policies", source)
        self.assertIn("please try a different prompt or send feedback", source)
        self.assertIn("you have not been charged for this generation", source)
        self.assertIn("we noticed some unusual activity", source)
        self.assertIn("browser-extension advisory", source)
        self.assertIn("please visit the help center for more information", source)
        self.assertIn("failurecontainerlimit: 200", source)
        self.assertIn("depth < 12", source)
        self.assertIn("scanfailurecontainers", source)
        self.assertIn(r"/\bfailed\b", source)
        self.assertNotIn(r"/\\bfailed\\b", source)
        self.assertIn(r"replace(/\s+/g", source)
        self.assertNotIn("document.queryselectorall", source)

    def test_failure_observation_includes_privacy_safe_detector_counters(self):
        source = inspect.getsource(FlowSession._generation_failure_observation)
        for field in (
            "failure_observer_active",
            "post_start_failure_mutation_count",
            "visible_failure_container_count",
            "current_attempt_failure_container_count",
            "unusual_signature_match_count",
            "generic_generation_signature_match_count",
            "last_failure_detection_reason",
        ):
            self.assertIn(field, source)


class PolicyFailureRunnerTests(unittest.TestCase):
    @staticmethod
    def batch():
        return Batch(
            batch_id="sha256:policy-failure-tests",
            source_schema="test",
            items=(
                BatchItem(order=1, visual_id="visual_1", prompt="prompt 1"),
                BatchItem(order=2, visual_id="visual_2", prompt="prompt 2"),
            ),
        )

    class Adapter:
        def __init__(self, generation_steps=()):
            self.generation_steps = list(generation_steps)
            self.events = []
            self.create_calls = 0
            self.generate_calls_by_order = {}
            self.session_identity = object()
            self.dom_epoch = 0
            self.browser_close_calls = 0
            self.browser_launch_calls = 0
            self.cdp_reconnect_calls = 0

        def create_flow_project(self):
            self.create_calls += 1
            self.events.append("create")
            return PROJECT

        def prepare_flow_project(self, *, project, settings):
            self.events.append(("prepare", project.project_id, settings.variants))

        def generate_one(self, *, item, settings, forbidden_candidate_ids=frozenset()):
            self.events.append(
                ("generate", item.order, item.prompt, frozenset(forbidden_candidate_ids))
            )
            attempt = self.generate_calls_by_order.get(item.order, 0) + 1
            self.generate_calls_by_order[item.order] = attempt
            if not self.generation_steps:
                raise AssertionError("unexpected generation")
            step = self.generation_steps.pop(0)
            if step == "policy":
                raise FlowPolicyRejected()
            if step == "generic":
                raise FlowGenericGenerationFailed()
            if step == "unusual":
                raise FlowUnusualActivity()
            if step == "usage":
                raise FlowUsageLimitReached()
            if step == "unknown":
                raise RuntimeError("unknown synthetic generation failure")
            if step != "success":
                raise AssertionError(f"unknown scripted step: {step}")
            candidate_id = f"order-{item.order}-attempt-{attempt}-v01"
            return GenerationResult(
                candidates=(
                    DiscoveredCandidate(
                        source_kind="rendered-image",
                        candidate_id=candidate_id,
                        asset_url=f"https://flow-content.google/image/{candidate_id}",
                    ),
                )
            )

        def generate_one_with_unusual_activity_recovery(
            self,
            *,
            item,
            settings,
            forbidden_candidate_ids,
            project,
            retry_started,
            generic_failure_retry_started,
            policy_failure_retry_started=None,
        ):
            unusual_retry_used = False
            while True:
                try:
                    return self.generate_one(
                        item=item,
                        settings=settings,
                        forbidden_candidate_ids=forbidden_candidate_ids,
                    )
                except FlowUnusualActivity:
                    if unusual_retry_used:
                        raise
                    self.events.append(("navigate_home", self.session_identity))
                    self.events.append(
                        ("reopen", project.project_id, self.session_identity)
                    )
                    self.dom_epoch += 1
                    self.events.append(("fresh_editor", self.dom_epoch))
                    retry_started()
                    self.events.append(("fresh_baseline", self.dom_epoch))
                    unusual_retry_used = True
                except FlowGenericGenerationFailed as exc:
                    if not generic_failure_retry_started(exc):
                        raise
                    self.events.append(("fresh_baseline", self.dom_epoch))

        def download_candidate(self, *, candidate, destination):
            self.events.append(("download", candidate.candidate_id))
            Image.new("RGB", (8, 6), (20, 30, 40)).save(destination, format="JPEG")
            return BrowserDownloadResult(
                candidate_id=str(candidate.candidate_id),
                staged_path=str(destination),
                browser_suggested_filename=f"{candidate.candidate_id}.jpeg",
                browser_download_guid=f"guid-{candidate.candidate_id}",
                mechanism="cdp_download_events",
            )

    def seed_managed_policy_failure(
        self,
        run_dir: Path,
        *,
        automatic_retry_count: int,
    ):
        batch = self.batch()
        state = initialize_run_state(batch, run_dir, require_flow_project=True)
        state.flow_project = PROJECT
        state.items[0].status = "failed"
        state.items[0].attempt_count = 1
        state.items[0].generation_error_code = FLOW_POLICY_REJECTED
        state.items[0].error = f"{FLOW_POLICY_REJECTED}: synthetic policy rejection"
        state.items[0].automatic_policy_retry_count = automatic_retry_count
        RunStateStore(run_dir / "run_state.json").save(state)
        return batch

    def seed_managed_generic_failure(
        self,
        run_dir: Path,
        *,
        automatic_retry_count: int,
    ):
        batch = self.batch()
        state = initialize_run_state(batch, run_dir, require_flow_project=True)
        state.flow_project = PROJECT
        state.items[0].status = "failed"
        state.items[0].attempt_count = automatic_retry_count + 1
        state.items[0].generation_error_code = FLOW_GENERIC_GENERATION_FAILED
        state.items[0].error = (
            f"{FLOW_GENERIC_GENERATION_FAILED}: synthetic generic failure"
        )
        state.items[0].automatic_generic_failure_retry_count = (
            automatic_retry_count
        )
        RunStateStore(run_dir / "run_state.json").save(state)
        return batch

    def test_first_generic_failure_retries_same_order_prompt_project_then_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("generic", "success", "success"))

            final, outcomes = execute_sequential_batch(
                self.batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            generate_events = [
                event
                for event in adapter.events
                if isinstance(event, tuple) and event[0] == "generate"
            ]
            self.assertEqual(
                [(event[1], event[2]) for event in generate_events],
                [(1, "prompt 1"), (1, "prompt 1"), (2, "prompt 2")],
            )
            self.assertEqual(adapter.create_calls, 1)
            self.assertEqual(final.flow_project, PROJECT)
            self.assertEqual(final.items[0].attempt_count, 2)
            self.assertEqual(
                final.items[0].automatic_generic_failure_retry_count,
                1,
            )
            self.assertEqual(final.items[0].status, "completed")
            self.assertEqual(final.items[0].download_status, "completed")
            self.assertEqual(final.items[1].attempt_count, 1)
            self.assertEqual(final.items[1].status, "completed")
            self.assertEqual([outcome.order for outcome in outcomes], [1, 2])
            self.assertEqual(outcomes[0].generation, "generated")
            self.assertNotIn("navigate_home", [event[0] for event in adapter.events if isinstance(event, tuple)])
            self.assertEqual(adapter.browser_close_calls, 0)
            self.assertEqual(adapter.browser_launch_calls, 0)
            self.assertEqual(adapter.cdp_reconnect_calls, 0)

    def test_two_generic_failures_use_two_durable_retries_then_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("generic", "generic", "success", "success"))

            final, _ = execute_sequential_batch(
                self.batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(adapter.generate_calls_by_order, {1: 3, 2: 1})
            self.assertEqual(final.items[0].attempt_count, 3)
            self.assertEqual(
                final.items[0].automatic_generic_failure_retry_count,
                2,
            )
            self.assertEqual(
                [event for event in adapter.events if event == ("fresh_baseline", 0)],
                [("fresh_baseline", 0), ("fresh_baseline", 0)],
            )
            self.assertFalse(
                any(
                    isinstance(event, tuple) and event[0] == "navigate_home"
                    for event in adapter.events
                )
            )

    def test_third_generic_failure_stops_before_download_and_next_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("generic", "generic", "generic", "success"))

            with self.assertRaisesRegex(
                FlowGenericGenerationFailed,
                FLOW_GENERIC_GENERATION_FAILED,
            ):
                execute_sequential_batch(
                    self.batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )

            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(adapter.generate_calls_by_order, {1: 3})
            self.assertFalse(
                any(
                    isinstance(event, tuple) and event[0] == "download"
                    for event in adapter.events
                )
            )
            self.assertEqual(state.items[0].status, "failed")
            self.assertEqual(
                state.items[0].generation_error_code,
                FLOW_GENERIC_GENERATION_FAILED,
            )
            self.assertEqual(state.items[0].attempt_count, 3)
            self.assertEqual(
                state.items[0].automatic_generic_failure_retry_count,
                2,
            )
            self.assertEqual(state.items[1].status, "pending")
            self.assertEqual(state.items[1].attempt_count, 0)

    def test_generic_retry_budget_survives_restart_and_resumes_remaining_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.seed_managed_generic_failure(
                run_dir,
                automatic_retry_count=1,
            )
            adapter = self.Adapter(("success", "success"))

            plan = plan_sequential_batch(
                batch,
                run_dir,
                GenerationSettings(variants=1),
            )
            self.assertEqual(
                plan.orders[0].generation_action,
                "auto_retry_generic_failure",
            )
            final, _ = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(adapter.create_calls, 0)
            self.assertEqual(adapter.events[0], ("prepare", PROJECT.project_id, 1))
            self.assertEqual(final.items[0].attempt_count, 3)
            self.assertEqual(
                final.items[0].automatic_generic_failure_retry_count,
                2,
            )
            self.assertEqual(final.items[0].status, "completed")

    def test_exhausted_generic_retry_budget_requires_manual_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.seed_managed_generic_failure(
                run_dir,
                automatic_retry_count=2,
            )
            adapter = self.Adapter(("success",))

            plan = plan_sequential_batch(
                batch,
                run_dir,
                GenerationSettings(variants=1),
            )
            self.assertEqual(plan.orders[0].generation_action, "blocked_retry_required")
            with self.assertRaisesRegex(ValueError, "retry required"):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            self.assertEqual(adapter.events, [])
            persisted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(
                persisted.items[0].automatic_generic_failure_retry_count,
                2,
            )
            self.assertEqual(persisted.items[0].attempt_count, 3)

    def test_first_policy_rejection_retries_same_prompt_project_then_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("policy", "success", "success"))
            retry_events = []

            final, outcomes = execute_sequential_batch(
                self.batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                policy_retry_event=lambda order, event: retry_events.append(
                    (order, event)
                ),
                manage_flow_project=True,
            )

            self.assertEqual(
                adapter.events[:4],
                [
                    "create",
                    ("prepare", PROJECT.project_id, 1),
                    ("generate", 1, "prompt 1", frozenset()),
                    ("generate", 1, "prompt 1", frozenset()),
                ],
            )
            self.assertEqual(adapter.events[4][0], "download")
            self.assertEqual(
                adapter.events[5],
                (
                    "generate",
                    2,
                    "prompt 2",
                    frozenset({"order-1-attempt-2-v01"}),
                ),
            )
            self.assertEqual(adapter.events[6][0], "download")
            self.assertEqual(adapter.create_calls, 1)
            self.assertEqual(final.flow_project, PROJECT)
            self.assertEqual(final.items[0].attempt_count, 2)
            self.assertEqual(final.items[0].automatic_policy_retry_count, 1)
            self.assertEqual(final.items[0].status, "completed")
            self.assertEqual(final.items[0].download_status, "completed")
            self.assertEqual(final.items[1].attempt_count, 1)
            self.assertEqual(final.items[1].status, "completed")
            self.assertEqual([outcome.order for outcome in outcomes], [1, 2])
            self.assertEqual(outcomes[0].generation, "retried_policy_automatic")
            self.assertEqual(
                retry_events,
                [
                    (1, "automatic_retry_started"),
                    (1, "automatic_retry_succeeded"),
                ],
            )
            candidate_ids = [
                candidate.candidate_id
                for item in final.items
                for candidate in item.candidates
            ]
            self.assertEqual(len(candidate_ids), len(set(candidate_ids)))

    def test_unusual_activity_recovers_same_session_project_order_then_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("unusual", "success", "success"))

            final, outcomes = execute_sequential_batch(
                self.batch(),
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(adapter.events[0], "create")
            self.assertEqual(adapter.events[1], ("prepare", PROJECT.project_id, 1))
            self.assertEqual(adapter.events[2][0:3], ("generate", 1, "prompt 1"))
            self.assertEqual(adapter.events[3], ("navigate_home", adapter.session_identity))
            self.assertEqual(
                adapter.events[4],
                ("reopen", PROJECT.project_id, adapter.session_identity),
            )
            self.assertEqual(adapter.events[5], ("fresh_editor", 1))
            self.assertEqual(adapter.events[6], ("fresh_baseline", 1))
            self.assertEqual(adapter.events[7][0:3], ("generate", 1, "prompt 1"))
            self.assertEqual(adapter.events[8][0], "download")
            self.assertEqual(adapter.events[9][0:3], ("generate", 2, "prompt 2"))
            self.assertEqual(adapter.events[10][0], "download")
            self.assertEqual(adapter.generate_calls_by_order, {1: 2, 2: 1})
            self.assertEqual(adapter.browser_close_calls, 0)
            self.assertEqual(adapter.browser_launch_calls, 0)
            self.assertEqual(adapter.cdp_reconnect_calls, 0)
            self.assertEqual(final.flow_project, PROJECT)
            self.assertEqual(final.items[0].attempt_count, 2)
            self.assertEqual(final.items[0].status, "completed")
            self.assertEqual(final.items[1].attempt_count, 1)
            self.assertEqual(final.items[1].status, "completed")
            self.assertEqual([outcome.order for outcome in outcomes], [1, 2])

    def test_repeated_unusual_activity_stops_same_order_before_next_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("unusual", "unusual", "success"))

            with self.assertRaises(FlowUnusualActivity):
                execute_sequential_batch(
                    self.batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )

            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(adapter.generate_calls_by_order, {1: 2})
            self.assertEqual(state.items[0].attempt_count, 2)
            self.assertEqual(state.items[0].status, "failed")
            self.assertEqual(
                state.items[0].generation_error_code,
                FLOW_UNUSUAL_ACTIVITY,
            )
            self.assertEqual(state.items[1].status, "pending")
            self.assertEqual(state.items[1].attempt_count, 0)

    def test_second_policy_rejection_stops_without_third_attempt_and_requires_manual_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            adapter = self.Adapter(("policy", "policy", "success"))
            retry_events = []

            with self.assertRaisesRegex(FlowPolicyRejected, FLOW_POLICY_REJECTED):
                execute_sequential_batch(
                    self.batch(),
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    policy_retry_event=lambda order, event: retry_events.append(
                        (order, event)
                    ),
                    manage_flow_project=True,
                )

            generate_events = [
                event
                for event in adapter.events
                if isinstance(event, tuple) and event[0] == "generate"
            ]
            self.assertEqual(len(generate_events), 2)
            self.assertFalse(
                any(
                    isinstance(event, tuple) and event[0] == "download"
                    for event in adapter.events
                )
            )
            self.assertEqual(adapter.generation_steps, ["success"])
            state = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(state.flow_project, PROJECT)
            self.assertEqual(state.items[0].status, "failed")
            self.assertEqual(state.items[0].generation_error_code, FLOW_POLICY_REJECTED)
            self.assertEqual(state.items[0].attempt_count, 2)
            self.assertEqual(state.items[0].automatic_policy_retry_count, 1)
            self.assertEqual(state.items[1].status, "pending")
            self.assertEqual(state.items[1].attempt_count, 0)
            self.assertEqual(
                retry_events,
                [
                    (1, "automatic_retry_started"),
                    (1, "automatic_retry_exhausted"),
                ],
            )

            resume = self.Adapter(("success",))
            with self.assertRaisesRegex(ValueError, "retry required"):
                execute_sequential_batch(
                    self.batch(),
                    run_dir,
                    resume,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            self.assertEqual(resume.events, [])
            self.assertEqual(resume.create_calls, 0)

            manual = self.Adapter(("success", "success"))
            completed, _ = execute_sequential_batch(
                self.batch(),
                run_dir,
                manual,
                GenerationSettings(variants=1),
                retry_failed_generation=True,
                manage_flow_project=True,
            )
            self.assertEqual(manual.create_calls, 0)
            self.assertEqual(manual.events[0], ("prepare", PROJECT.project_id, 1))
            self.assertEqual(completed.items[0].attempt_count, 3)
            self.assertEqual(completed.items[0].automatic_policy_retry_count, 1)

    def test_resume_after_first_persisted_rejection_uses_one_retry_without_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.seed_managed_policy_failure(
                run_dir,
                automatic_retry_count=0,
            )
            adapter = self.Adapter(("success", "success"))

            final, _ = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            self.assertEqual(adapter.create_calls, 0)
            self.assertEqual(adapter.events[0], ("prepare", PROJECT.project_id, 1))
            self.assertEqual(adapter.events[1][1:3], (1, "prompt 1"))
            self.assertEqual(final.items[0].attempt_count, 2)
            self.assertEqual(final.items[0].automatic_policy_retry_count, 1)

    def test_consumed_retry_survives_restart_and_cannot_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.seed_managed_policy_failure(
                run_dir,
                automatic_retry_count=1,
            )
            adapter = self.Adapter(("success",))

            plan = plan_sequential_batch(
                batch,
                run_dir,
                GenerationSettings(variants=1),
            )
            self.assertEqual(plan.orders[0].generation_action, "blocked_retry_required")
            with self.assertRaisesRegex(ValueError, "retry required"):
                execute_sequential_batch(
                    batch,
                    run_dir,
                    adapter,
                    GenerationSettings(variants=1),
                    manage_flow_project=True,
                )
            self.assertEqual(adapter.events, [])
            persisted = RunStateStore(run_dir / "run_state.json").load()
            self.assertEqual(persisted.items[0].automatic_policy_retry_count, 1)
            self.assertEqual(persisted.items[0].attempt_count, 1)

    def test_other_generation_failures_never_receive_policy_retry(self):
        cases = (
            ("usage", FlowUsageLimitReached, WAITING_FOR_FLOW_LIMIT_RESET),
            ("unknown", RuntimeError, "failed"),
        )
        for step, error_type, expected_status in cases:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                adapter = self.Adapter((step, "success"))
                retry_events = []
                with self.assertRaises(error_type):
                    execute_sequential_batch(
                        self.batch(),
                        run_dir,
                        adapter,
                        GenerationSettings(variants=1),
                        policy_retry_event=lambda order, event: retry_events.append(
                            (order, event)
                        ),
                        manage_flow_project=True,
                    )

                generate_events = [
                    event
                    for event in adapter.events
                    if isinstance(event, tuple) and event[0] == "generate"
                ]
                self.assertEqual(len(generate_events), 1)
                self.assertEqual(retry_events, [])
                state = RunStateStore(run_dir / "run_state.json").load()
                self.assertEqual(state.items[0].status, expected_status)
                self.assertEqual(state.items[0].automatic_policy_retry_count, 0)

    def test_completed_generation_and_download_only_resume_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            batch = self.batch()
            state = initialize_run_state(batch, run_dir, require_flow_project=True)
            state.flow_project = PROJECT
            item = state.items[0]
            item.status = "completed"
            item.attempt_count = 1
            item.candidates = [
                CandidateRecord(
                    variant=1,
                    suggested_filename="0001__visual_1__v01.png",
                    source_kind="rendered-image",
                    candidate_id="persisted-order-1-v01",
                    asset_url="https://flow-content.google/image/persisted-order-1-v01",
                )
            ]
            RunStateStore(run_dir / "run_state.json").save(state)
            adapter = self.Adapter(("success",))

            final, outcomes = execute_sequential_batch(
                batch,
                run_dir,
                adapter,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )

            order_one_generation = [
                event
                for event in adapter.events
                if isinstance(event, tuple)
                and event[0] == "generate"
                and event[1] == 1
            ]
            self.assertEqual(order_one_generation, [])
            self.assertEqual(outcomes[0].generation, "skipped_completed")
            self.assertEqual(outcomes[0].downloads, "downloaded")
            self.assertEqual(final.items[0].attempt_count, 1)
            self.assertEqual(final.items[0].download_status, "completed")

            resumed = self.Adapter(())
            resumed_final, resumed_outcomes = execute_sequential_batch(
                batch,
                run_dir,
                resumed,
                GenerationSettings(variants=1),
                manage_flow_project=True,
            )
            self.assertEqual(
                resumed.events,
                [("prepare", PROJECT.project_id, 1)],
            )
            self.assertTrue(
                all(
                    outcome.generation == "skipped_completed"
                    for outcome in resumed_outcomes
                )
            )
            self.assertTrue(
                all(outcome.downloads == "skipped_valid" for outcome in resumed_outcomes)
            )
            self.assertEqual(resumed_final.flow_project, PROJECT)

    def test_retry_orchestration_introduces_no_browser_activation_shortcuts(self):
        source = inspect.getsource(execute_sequential_batch).casefold()
        self.assertNotIn("force", source)
        self.assertNotIn("javascript", source)
        self.assertNotIn("bringtofront", source)
        self.assertNotIn(".click", source)

    def test_unusual_activity_recovery_requires_no_human_input(self):
        sources = (
            inspect.getsource(execute_sequential_batch),
            inspect.getsource(FlowSession.generate_one_with_unusual_activity_recovery),
            inspect.getsource(FlowSession._recover_unusual_activity_in_open_session),
        )
        combined = "\n".join(sources).casefold()
        self.assertNotIn("input(", combined)
        self.assertNotIn("create_flow_project", combined)
        self.assertNotIn("connect_over_cdp", combined)
        self.assertNotIn("open_for_manual_reconnaissance", combined)

    def test_generic_failure_retry_uses_no_failure_card_or_human_input_path(self):
        source = inspect.getsource(
            FlowSession.generate_one_with_unusual_activity_recovery
        ).casefold()
        self.assertNotIn("input(", source)
        self.assertNotIn(".click", source)
        self.assertNotIn("force=", source)
        self.assertNotIn("javascript", source)
        self.assertNotIn("retry button", source)
        self.assertNotIn("create_flow_project", source)
        self.assertNotIn("connect_over_cdp", source)


if __name__ == "__main__":
    unittest.main()

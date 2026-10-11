"""Offline execution of the real observer with descendant-aggregating DOM text."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nano_batch.adapters.flow import (
    FlowAutomationError, FlowSession, FlowGenericGenerationFailed,
    FlowHighDemand, FlowPolicyRejected, FlowUnusualActivity, FlowUsageLimitReached,
)
from nano_batch.models import GenerationSettings
from nano_batch.runner import execute_sequential_batch, plan_sequential_batch
from nano_batch.state import RunStateStore
from tests import test_flow_explicit_failures as explicit_failures
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE,
    UNUSUAL_ACTIVITY_FAILURE, UNUSUAL_ACTIVITY_EXTENSION_FAILURE, PROJECT_URL,
    _generation_failure_observer_scripts,
)

_DOM = r"""
class Element {
  constructor(tag, text = '', attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.ownText = text;
    this.attributes = attributes;
    this.parentElement = null;
    this.children = [];
  }
  append(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  get isConnected() {
    let root = this;
    while (root.parentElement) root = root.parentElement;
    return root.tagName === 'BODY';
  }
  // Real DOM ancestors expose descendant text, including unchanged sibling cards.
  get innerText() {
    return [this.ownText, ...this.children.map(child => child.innerText)]
      .filter(Boolean).join('\n');
  }
  get textContent() {
    return this.ownText + this.children.map(child => child.textContent).join('');
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  getBoundingClientRect() { return {x: 0, y: 0, width: this.hidden ? 0 : 200, height: 80}; }
  matches(selector) {
    return selector.split(',').some(part => {
      const rule = part.trim();
      const attribute = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(rule);
      if (attribute) {
        const value = this.getAttribute(attribute[1]);
        return attribute[2] === undefined ? value !== null : value === attribute[2];
      }
      return this.tagName.toLowerCase() === rule;
    });
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  contains(element) {
    return this === element || this.children.some(child => child.contains(element));
  }
}
class MutationObserver {
  constructor(callback) { this.callback = callback; }
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
}
globalThis.Element = Element;
globalThis.MutationObserver = MutationObserver;
globalThis.getComputedStyle = () => ({visibility: 'visible', display: 'block'});
let clock = 100;
globalThis.performance = {now: () => ++clock};
globalThis.window = {};
const body = new Element('body');
globalThis.document = {body, createTreeWalker(root) {
  const nodes = [];
  const visit = element => {
    for (const child of element.children) { nodes.push(child); visit(child); }
  };
  visit(root);
  return {nextNode: () => nodes.shift() || null};
}};
const exec = script => (0, eval)('(' + script + ')')();


"""


def _run_case(setup, actions, *, failure=GENERIC_GENERATION_FAILURE, extra=""):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for offline observer tests.")
    begin, arm, poll = _generation_failure_observer_scripts()
    harness = "const [begin, arm, poll, failure] = " + json.dumps([begin, arm, poll, failure]) + ";\n" + _DOM
    harness += """
const history = body.append(new Element('section'));
const add = (parent, child) => {
  parent.append(child);
  state.observer.callback([{type: 'childList', target: parent, addedNodes: [child], removedNodes: []}]);
  return child;
};
const change = (element, text) => {
  const oldValue = element.ownText;
  element.ownText = text;
  state.observer.callback([{type: 'characterData', target: {parentElement: element, textContent: text}, oldValue}]);
};
const label = (element, text) => {
  const oldValue = element.getAttribute('aria-label');
  element.attributes['aria-label'] = text;
  state.observer.callback([{type: 'attributes', target: element, attributeName: 'aria-label', oldValue}]);
};
const detach = element => {
  const parent = element.parentElement;
  parent.children = parent.children.filter(child => child !== element);
  element.parentElement = null;
};
"""
    harness += setup + "\nexec(begin); exec(arm); const state = window.__nanoBatchCurrentGenerationAttempt;\n"
    harness += "const before = exec(poll);\n" + actions
    harness += "\nconst after = exec(poll); process.stdout.write(JSON.stringify({before, after, " + extra + "}));"
    completed = subprocess.run([node, "-"], input=harness, text=True, encoding="utf-8",
                               capture_output=True, timeout=15, check=True)
    return json.loads(completed.stdout)


class HistoricalFailureAttributionTests(unittest.TestCase):
    def assert_ignored(self, result):
        self.assertEqual(result["after"]["failures"], [])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def assert_ambiguous(self, result):
        self.assertEqual(result["after"]["failures"], [])
        self.assertTrue(result["after"]["failure_attribution_ambiguous"])
        self.assertEqual(result["after"]["diagnostics"]["last_failure_detection_reason"],
                         "failure_attribution_ambiguous")

    def test_new_sibling_progress_does_not_reclassify_unchanged_historical_failure(self):
        result = _run_case("""
const oldCard = history.append(new Element('article'));
oldCard.append(new Element('p', failure));
const currentCard = history.append(new Element('article'));
const oldBefore = [oldCard.innerText, oldCard.textContent, oldCard.getAttribute('aria-label')];
""", """
const progress = add(currentCard, new Element('div', 'Generating image 20%', {role: 'progressbar'}));
""", extra="""
 historicalCardUnchanged: JSON.stringify(oldBefore) === JSON.stringify([oldCard.innerText, oldCard.textContent, oldCard.getAttribute('aria-label')]),
 mutationTargetContainsHistoricalCard: currentCard.contains(oldCard),
 sharedAncestorContainsBoth: history.contains(oldCard) && history.contains(progress),
 sharedAncestorHasBothTexts: history.innerText.includes(failure) && history.innerText.includes(progress.innerText),
 progressRegionCount: state.progressRegionCount,
 retainedSharedAncestor: state.failureContainers.includes(history),
""")
        self.assertEqual(result["before"]["failures"], [])
        self.assertTrue(result["historicalCardUnchanged"])
        self.assertFalse(result["mutationTargetContainsHistoricalCard"])
        self.assertTrue(result["sharedAncestorContainsBoth"])
        self.assertTrue(result["sharedAncestorHasBothTexts"])
        self.assertEqual(result["progressRegionCount"], 1)
        self.assertFalse(result["retainedSharedAncestor"])
        self.assertEqual(result["after"]["diagnostics"]["generation_dom_mutation_count"], 1)
        self.assert_ignored(result)

    def test_historical_style_and_incidental_text_changes_are_ignored(self):
        result = _run_case("const old = history.append(new Element('p', failure));", """
state.observer.callback([{type: 'attributes', target: old, attributeName: 'class', oldValue: ''}]);
change(old, failure.toUpperCase().replace(/ /g, '  ') + ' Generating image 30%');
""")
        self.assert_ignored(result)

    def test_reinserting_same_historical_node_is_ignored(self):
        self.assert_ignored(_run_case("const old = history.append(new Element('p', failure));",
                                     "detach(old); add(history, old);"))

    def test_historical_remount_cannot_be_proven_current(self):
        for failure in (GENERIC_GENERATION_FAILURE, POLICY_FAILURE, HIGH_DEMAND_FAILURE,
                        UNUSUAL_ACTIVITY_FAILURE, UNUSUAL_ACTIVITY_EXTENSION_FAILURE):
            with self.subTest(failure=failure):
                result = _run_case("const old = history.append(new Element('article', failure));",
                                   "detach(old); add(history, new Element('article', failure));",
                                   failure=failure)
                self.assert_ambiguous(result)

    def test_nested_remount_with_case_and_whitespace_changes_is_ambiguous(self):
        self.assert_ambiguous(_run_case("""
const old = history.append(new Element('article'));
old.append(new Element('p', failure));
""", r"""
detach(old);
const replacement = new Element('article');
replacement.append(new Element('div')).append(new Element('p', failure.toUpperCase().replace(/ /g, '\n')));
add(history, replacement);
"""))

    def test_remounted_partial_history_and_shared_progress_do_not_complete_a_failure(self):
        self.assert_ignored(_run_case("""
const old = history.append(new Element('article', 'Failed. Sorry, this image failed to generate.'));
const current = history.append(new Element('article'));
""", """
detach(old); add(history, new Element('article', 'Failed. Sorry, this image failed to generate.'));
add(current, new Element('div', 'Generating', {role: 'progressbar'}));
"""))

    def test_new_failures_keep_all_existing_classifications(self):
        cases = (
            (GENERIC_GENERATION_FAILURE, FlowGenericGenerationFailed),
            (POLICY_FAILURE, FlowPolicyRejected), (HIGH_DEMAND_FAILURE, FlowHighDemand),
            (UNUSUAL_ACTIVITY_FAILURE, FlowUnusualActivity),
            (UNUSUAL_ACTIVITY_EXTENSION_FAILURE, FlowUnusualActivity),
            ('Failed. You have reached your usage limit.', FlowUsageLimitReached),
        )
        for failure, error_type in cases:
            with self.subTest(error=error_type.__name__, failure=failure):
                result = _run_case('', "add(history, new Element('article', failure));", failure=failure)
                self.assertFalse(result["after"]["failure_attribution_ambiguous"])
                session = FlowSession(PROJECT_URL, Path('unused-profile'))
                session.page = MagicMock()
                session.page.evaluate.return_value = result["after"]
                with self.assertRaises(error_type):
                    session._raise_for_current_generation_attempt_failure()

    def test_existing_nonfailure_surface_can_change_to_identical_historical_wording(self):
        result = _run_case("""
history.append(new Element('article', failure));
const current = history.append(new Element('article'));
const message = current.append(new Element('p', 'Generating image'));
""", "change(message, failure);")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_progress_to_identical_failure_in_separate_card_is_current(self):
        for role in ('progressbar', 'status'):
            with self.subTest(role=role):
                result = _run_case("""
history.append(new Element('article', failure));
const current = history.append(new Element('article'));
""", """
const progress = add(current, new Element('div', 'Generating image', {role: ROLE}));
detach(progress); add(current, new Element('p', failure));
""".replace('ROLE', json.dumps(role)))
                self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
                self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_shared_history_progress_does_not_qualify_identical_added_failure(self):
        self.assert_ambiguous(_run_case("history.append(new Element('article', failure));", """
add(history, new Element('div', 'Generating', {role: 'progressbar'}));
add(history, new Element('article', failure));
"""))

    def test_history_clearing_and_progress_do_not_authorize_historical_remount(self):
        self.assert_ambiguous(_run_case("""
history.attributes.role = 'status';
const old = history.append(new Element('article', failure));
""", """
detach(old);
add(history, new Element('div', 'Generating', {role: 'progressbar'}));
const replacement = new Element('div');
replacement.append(new Element('p', failure));
add(history, replacement);
"""))

    def test_existing_progress_update_qualifies_same_card_transition(self):
        result = _run_case("""
history.append(new Element('article', failure));
const current = history.append(new Element('article'));
const progress = current.append(new Element('div', 'Generating 10%', {role: 'progressbar'}));
""", "change(progress, 'Generating 20%'); detach(progress); add(current, new Element('p', failure));")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_same_message_node_can_fail_identically_after_local_content_reset(self):
        result = _run_case("const message = history.append(new Element('p', failure));", """
change(message, 'Generating image');
const reset = exec(poll);
change(message, failure);
""", extra="resetFailures: reset.failures,")
        self.assertEqual(result["resetFailures"], [])
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_clauses_cannot_be_assembled_across_separate_cards(self):
        self.assert_ignored(_run_case("""
history.append(new Element('article', 'Sorry, this image failed to generate. You have not been charged for this generation.'));
""", "add(history, new Element('article', 'Failed.'));"))

    def test_unmarked_history_cannot_combine_old_partial_and_new_sibling_clauses(self):
        for tag, attributes in [('SECTION', '{}'), ('ARTICLE', '{}'),
                                ('SECTION', '{role: "status"}')]:
            with self.subTest(tag=tag, attributes=attributes):
                setup = 'history.tagName = ' + json.dumps(tag) + '; history.attributes = ' + attributes + ';'
                self.assert_ambiguous(_run_case(setup + """
history.append(new Element('div', 'Failed.'));
const current = history.append(new Element('div'));
""", "add(current, new Element('span', 'Sorry, this image failed to generate. You have not been charged for this generation.'));"))

    def test_owned_nested_partial_message_can_receive_new_completion_clause(self):
        result = _run_case("""
const current = history.append(new Element('article'));
const message = current.append(new Element('div'));
""", """
add(message, new Element('span', 'Failed. Sorry, this image failed to generate. '));
const partial = exec(poll);
add(message, new Element('span', 'You have not been charged for this generation.'));
""", extra="partialFailures: partial.failures,")
        self.assertEqual(result["partialFailures"], [])
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_genuine_deep_nested_message_preserves_inline_clauses(self):
        result = _run_case("const current = history.append(new Element('article'));", """
const message = new Element('div');
message.append(new Element('span', 'Failed. '));
let deep = message;
for (let i = 0; i < 8; i++) deep = deep.append(new Element('div'));
deep.append(new Element('span', 'Sorry, this image failed to generate. '));
message.append(new Element('span', 'You have not been charged for this generation.'));
add(current, message);
""")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_deep_text_mutation_completes_current_message_on_first_poll(self):
        result = _run_case("""
const current = history.append(new Element('article'));
const message = current.append(new Element('div'));
""", """
add(message, new Element('span', 'Failed. '));
let deep = new Element('div');
const root = deep;
for (let i = 0; i < 8; i++) deep = deep.append(new Element('div'));
const leaf = deep.append(new Element('span'));
add(message, root);
add(message, new Element('span', ' You have not been charged for this generation.'));
const partial = exec(poll);
change(leaf, 'Sorry, this image failed to generate.');
""", extra="partialFailures: partial.failures,")
        self.assertEqual(result["partialFailures"], [])
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_partial_message_can_be_completed_by_new_failure_content(self):
        result = _run_case("""
const current = history.append(new Element('article'));
const message = current.append(new Element('p', 'Failed. Sorry, this image failed to generate.'));
""", "change(message, failure);")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])

    def test_existing_historical_failure_can_meaningfully_change_classification(self):
        result = _run_case("const message = history.append(new Element('p', failure));",
                           'change(message, ' + json.dumps(POLICY_FAILURE) + ');')
        self.assertEqual(result["after"]["failures"], [POLICY_FAILURE])

    def test_changed_accessibility_label_is_independent_failure_evidence(self):
        result = _run_case("const message = history.append(new Element('div', 'Unrelated text'));",
                           "label(message, failure);")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])

    def test_complete_label_does_not_inherit_a_nested_historical_message(self):
        result = _run_case("""
const message = history.append(new Element('div'));
message.append(new Element('p', failure));
""", 'label(message, ' + json.dumps(POLICY_FAILURE) + ');')
        self.assertEqual(result["after"]["failures"], [POLICY_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_partial_label_and_text_channels_cannot_be_combined(self):
        self.assert_ignored(_run_case("const message = history.append(new Element('div'));", """
change(message, 'Sorry, this image failed to generate. You have not been charged for this generation.');
label(message, 'Failed.');
"""))

    def test_revealing_hidden_historical_message_is_not_new_content(self):
        self.assert_ignored(_run_case("const message = history.append(new Element('p', failure)); message.hidden = true;", """
message.hidden = false;
state.observer.callback([{type: 'attributes', target: message, attributeName: 'style', oldValue: ''}]);
"""))

    def test_new_hidden_message_retains_provenance_until_visible_poll(self):
        result = _run_case('', """
const message = new Element('p', failure); message.hidden = true; add(history, message);
const hidden = exec(poll);
message.hidden = false;
state.observer.callback([{type: 'attributes', target: message, attributeName: 'style', oldValue: ''}]);
""", extra="hiddenFailures: hidden.failures,")
        self.assertEqual(result["hiddenFailures"], [])
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])

    def test_rearm_preserves_original_attempt_baseline_and_progress_provenance(self):
        result = _run_case("""
history.append(new Element('article', failure));
const current = history.append(new Element('article'));
""", """
const progress = add(current, new Element('div', 'Generating', {role: 'progressbar'}));
state.active = false; exec(arm);
detach(progress); add(current, new Element('p', failure));
""")
        self.assertEqual(result["after"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result["after"]["failure_attribution_ambiguous"])

    def test_incomplete_baseline_fails_closed_on_unknown_failure(self):
        self.assert_ambiguous(_run_case("for (let i = 0; i < 10001; i++) history.append(new Element('div'));",
                                       "add(history, new Element('p', failure));"))

    def test_retention_overflow_reports_ambiguity_instead_of_false_success(self):
        result = _run_case('', "for (let i = 0; i < 201; i++) add(history, new Element('p', failure));",
                           extra="retained: state.failureContainers.length,")
        self.assertEqual(result["retained"], 200)
        self.assertTrue(result["after"]["failure_attribution_ambiguous"])


class AmbiguousFailureHandlingTests(unittest.TestCase):
    def snapshot(self):
        return _run_case("const old = history.append(new Element('article', failure));",
                         "detach(old); add(history, new Element('article', failure));")["after"]

    def test_ambiguous_result_stops_before_candidate_success_or_further_waits(self):
        session = FlowSession(PROJECT_URL, Path('unused-profile'))
        session.page = MagicMock()
        session.page.evaluate.return_value = self.snapshot()
        with patch.object(session, 'discover_candidates') as discover, \
             patch.object(session, '_generation_window_watchdog', return_value=None), \
             patch('nano_batch.adapters.flow.time.monotonic', side_effect=[0.0, 0.0]), \
             self.assertRaisesRegex(FlowAutomationError, 'FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS'):
            session.wait_for_result(set(), 300, expected_count=1)
        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()

    def test_ambiguous_ack_stops_before_success_or_another_start(self):
        session = FlowSession(PROJECT_URL, Path('unused-profile'))
        session.page = MagicMock()
        session.page.evaluate.return_value = self.snapshot()
        with patch.object(session, 'discover_candidates') as discover, \
             patch('nano_batch.adapters.flow.time.monotonic', side_effect=[0.0, 0.0]), \
             self.assertRaisesRegex(FlowAutomationError, 'FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS'):
            session._wait_for_generation_start(prompt_input=MagicMock(), intended_prompt='synthetic',
                                               baseline_fingerprints=set(), baseline_progress=set())
        discover.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()
        session.page.mouse.down.assert_not_called()

    def test_ambiguity_persists_failure_without_consuming_automatic_retry_budgets(self):
        snapshot = self.snapshot()
        class Adapter(explicit_failures.PolicyFailureRunnerTests.Adapter):
            def generate_one(self, **kwargs):
                self.events.append(('generate', kwargs['item'].order))
                session = FlowSession(PROJECT_URL, Path('unused-profile'))
                session.page = MagicMock()
                session.page.evaluate.return_value = snapshot
                session._raise_for_current_generation_attempt_failure()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / 'run'
            adapter = Adapter()
            with self.assertRaisesRegex(FlowAutomationError, 'FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS'):
                execute_sequential_batch(explicit_failures.PolicyFailureRunnerTests.batch(), run_dir,
                                         adapter, GenerationSettings(variants=1),
                                         manage_flow_project=True)
            saved = RunStateStore(run_dir / 'run_state.json').load()
            self.assertEqual(saved.items[0].status, 'failed')
            self.assertIn('FLOW_FAILURE_ATTRIBUTION_AMBIGUOUS', saved.items[0].error)
            self.assertEqual(saved.items[0].attempt_count, 1)
            self.assertEqual(saved.items[0].automatic_generic_failure_retry_count, 0)
            self.assertEqual(saved.items[0].automatic_policy_retry_count, 0)
            self.assertEqual(saved.items[0].candidates, [])
            self.assertEqual(saved.items[1].status, 'pending')
            self.assertEqual([event for event in adapter.events if isinstance(event, tuple) and event[0] == 'generate'], [('generate', 1)])
            plan = plan_sequential_batch(explicit_failures.PolicyFailureRunnerTests.batch(),
                                         run_dir, GenerationSettings(variants=1))
            self.assertEqual(plan.orders[0].generation_action, 'blocked_retry_required')
            self.assertFalse(any(isinstance(event, tuple) and event[0] in
                                 {'navigate_home', 'reopen', 'fresh_baseline', 'download'}
                                 for event in adapter.events))


if __name__ == '__main__':
    unittest.main()

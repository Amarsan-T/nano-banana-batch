"""Known historical tile identities must not authorize current failure retries."""

import unittest

from tests.test_flow_failure_attribution import _run_case
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE,
    UNUSUAL_ACTIVITY_FAILURE,
)


OLD_ID = "00000000-0000-4000-8000-000000000001"
NEW_ID = "00000000-0000-4000-8000-000000000002"
OLD_TILE = f"""
const old = history.append(new Element('flow-image-tile'));
old.append(new Element('img', '', {{'data-media-id': '{OLD_ID}'}}));
"""


class HistoricalUuidFailureTests(unittest.TestCase):
    def assert_ambiguous(self, result):
        self.assertEqual(result['after']['failures'], [])
        self.assertTrue(result['after']['failure_attribution_ambiguous'])

    def test_late_failure_on_pre_start_uuid_is_not_a_current_exact_failure(self):
        for failure in (GENERIC_GENERATION_FAILURE, POLICY_FAILURE, HIGH_DEMAND_FAILURE,
                        UNUSUAL_ACTIVITY_FAILURE, 'Failed. You have reached your usage limit.'):
            with self.subTest(failure=failure):
                self.assert_ambiguous(_run_case(
                    OLD_TILE, "add(old, new Element('p', failure));", failure=failure,
                ))

    def test_remount_with_known_old_uuid_and_novel_failure_is_ambiguous(self):
        self.assert_ambiguous(_run_case(OLD_TILE, f"""
detach(old);
const replacement = new Element('flow-image-tile');
replacement.append(new Element('img', '', {{'data-media-id': '{OLD_ID}'}}));
replacement.append(new Element('p', failure));
add(history, replacement);
"""))

    def test_local_progress_cannot_reassign_a_pre_start_uuid(self):
        self.assert_ambiguous(_run_case(OLD_TILE, """
const progress = add(old, new Element('div', 'Generating image', {role: 'status'}));
detach(progress); add(old, new Element('p', failure));
"""))

    def test_deeply_nested_failure_keeps_historical_tile_ownership(self):
        self.assert_ambiguous(_run_case(OLD_TILE, """
let parent = old;
for (let i = 0; i < 20; i++) parent = parent.append(new Element('div'));
add(parent, new Element('p', failure));
"""))

    def test_unchanged_historical_failure_remains_ignored(self):
        result = _run_case(OLD_TILE + "old.append(new Element('p', failure));", """
add(history, new Element('div', 'Unrelated status update'));
""")
        self.assertEqual(result['after']['failures'], [])
        self.assertFalse(result['after']['failure_attribution_ambiguous'])

    def test_new_uuid_sibling_keeps_genuine_failure_detection(self):
        result = _run_case(OLD_TILE, f"""
const current = new Element('flow-image-tile');
current.append(new Element('img', '', {{'data-media-id': '{NEW_ID}'}}));
current.append(new Element('p', failure));
add(history, current);
""")
        self.assertEqual(result['after']['failures'], [GENERIC_GENERATION_FAILURE])
        self.assertFalse(result['after']['failure_attribution_ambiguous'])

    def test_rearm_keeps_original_historical_uuid_evidence(self):
        self.assert_ambiguous(_run_case(OLD_TILE, """
state.active = false; exec(arm);
add(old, new Element('p', failure));
"""))

    def test_historical_uuid_on_an_ancestor_is_still_ownership_evidence(self):
        self.assert_ambiguous(_run_case(f"""
const old = history.append(new Element('div', '', {{'data-media-id': '{OLD_ID}'}}));
""", "add(old, new Element('p', failure));"))

    def test_case_changed_historical_uuid_remount_is_ambiguous(self):
        self.assert_ambiguous(_run_case("""
const old = history.append(new Element('flow-image-tile'));
old.append(new Element('img', '', {'data-media-id': '00000000-0000-4000-8000-000000000abc'}));
""", """
detach(old);
const replacement = new Element('flow-image-tile');
replacement.append(new Element('img', '', {'data-media-id': '00000000-0000-4000-8000-000000000abc'.toUpperCase()}));
replacement.append(new Element('p', failure)); add(history, replacement);
"""))

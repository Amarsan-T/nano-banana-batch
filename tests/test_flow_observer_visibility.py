"""Offline visibility reuse checks against the ec335f2 observer behavior."""

import unittest
from unittest.mock import patch

from tests import test_flow_observer_read_timing as read_timing
from tests import test_flow_observer_text_reuse as text_reuse
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE,
    UNUSUAL_ACTIVITY_FAILURE, UNUSUAL_ACTIVITY_EXTENSION_FAILURE,
    _generation_failure_observer_scripts,
)


def original_visibility(begin):
    """Restore the original guard/calls; retain all other observer logic."""
    begin = begin.replace(
        "const detectFailureIn = (element, visibilityVerified = false) => {\n"
        "                if (!(element instanceof Element) || !element.isConnected ||\n"
        "                    (!visibilityVerified && !visible(element))) {",
        "state.detectFailureIn = (element) => {\n"
        "                if (!(element instanceof Element) || !element.isConnected || !visible(element)) {",
    )
    begin = begin.replace(
        "              // Independent callers always verify their own current visibility.\n"
        "              state.detectFailureIn = (element) => detectFailureIn(element);\n", "",
    )
    return begin.replace("detectFailureIn(element, true)", "state.detectFailureIn(element)")


def probe(*, original=False, **kwargs):
    begin, arm, poll = _generation_failure_observer_scripts()
    if original:
        begin = original_visibility(begin)
    with patch.object(text_reuse, "_generation_failure_observer_scripts",
                      return_value=(begin, arm, poll)), patch.object(
                          text_reuse, "reference_begin", side_effect=original_visibility):
        return text_reuse.probe(**kwargs)


def cards_fixture(count=200, *, historical=False):
    setup = (
        "const cards=Array.from({length:COUNT},()=>history.append(new Element('article'))"
        ".append(new Element('p',INITIAL)));"
    ).replace("COUNT", str(count)).replace("INITIAL", "exact" if historical else "''")
    prepare = """
for(const card of cards)card.ownText=exact;
state.observer.callback(cards.map(card=>RECORD));
""".replace("RECORD", (
        "({type:'attributes',target:card,attributeName:'class',oldValue:''})" if historical else
        "({type:'characterData',target:{parentElement:card,textContent:card.ownText},oldValue:''})"
    ))
    return dict(setup=setup, prepare=prepare, actions="take();")


class ObserverVisibilityTests(unittest.TestCase):
    def equivalent(self, **kwargs):
        old, new = probe(original=True, **kwargs), probe(**kwargs)
        self.assertEqual(new["snapshots"], old["snapshots"])
        for key in ("requests", "computations", "aggregate_characters", "connected_reads",
                    "selector_calls", "selector_visits"):
            self.assertEqual(new["operations"].get(key, 0), old["operations"].get(key, 0))
        return old, new

    def test_200_visible_containers_use_one_style_and_geometry_read_each(self):
        for debug in (False, True):
            with self.subTest(debug=debug):
                old, new = self.equivalent(debug=debug, **cards_fixture())
                self.assertEqual(old["operations"]["style_reads"], 400)
                self.assertEqual(old["operations"]["geometry_reads"], 400)
                self.assertEqual(new["operations"]["style_reads"], 200)
                self.assertEqual(new["operations"]["geometry_reads"], 200)
                self.assertEqual(new["final"]["visible"], 200)
                self.assertEqual(new["final"]["current"], 200)

    def test_debug_stage_counts_measure_the_single_real_read(self):
        result = read_timing.ObserverReadTimingTests().browser_read(count=200)
        raw = result["result"]["diagnostics"]["observer_read_internal_timing"]
        self.assertEqual(raw["containers_scanned"], 200)
        for name in ("style_read", "geometry_read", "visibility_test"):
            self.assertEqual(raw["stages"][name]["calls"], 200)
        self.assertEqual(raw["container_sample_count"], 128)
        self.assertEqual(result["calls"]["style_read"], 200)
        self.assertEqual(result["calls"]["geometry_read"], 200)

    def test_hidden_containers_are_not_read_twice_or_qualified(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                fixture = cards_fixture(20, historical=historical)
                fixture["prepare"] += "for(const card of cards)card.hidden=true;"
                old, new = self.equivalent(**fixture)
                self.assertEqual(new["final"]["failures"], [])
                self.assertEqual(new["final"]["visible"], 0)
                self.assertEqual(new["operations"]["style_reads"], 20)
                self.assertEqual(new["operations"], old["operations"])

    def test_direct_calls_always_check_visibility_even_with_extra_argument(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                fixture = cards_fixture(1)
                fixture["prepare"] += "cards[0].hidden=" + str(hidden).lower() + ";"
                fixture["actions"] = "state.detectFailureIn(cards[0],true);"
                old, new = self.equivalent(**fixture)
                self.assertEqual(new["operations"]["style_reads"], 1)
                self.assertEqual(new["operations"]["geometry_reads"], 1)
                self.assertEqual(bool(new["final"]["failures"]), not hidden)
                self.assertEqual(new["operations"], old["operations"])

    def test_direct_detached_and_non_element_calls_keep_guard_order(self):
        fixture = cards_fixture(1)
        fixture["prepare"] += "detach(cards[0]);"
        fixture["actions"] = "state.detectFailureIn(cards[0]);state.detectFailureIn(null);state.detectFailureIn({});"
        old, new = self.equivalent(**fixture)
        self.assertEqual(new["operations"].get("style_reads", 0), 0)
        self.assertEqual(new["operations"].get("geometry_reads", 0), 0)
        self.assertEqual(new["final"]["failures"], [])

    def test_each_poll_sees_hidden_revealed_and_changed_geometry(self):
        self.equivalent(setup="const p=history.append(new Element('p'));", actions="""
p.hidden=true;change(p,exact);take();
p.hidden=false;take();p.hidden=true;take();p.hidden=false;take();
""")

    def test_style_visibility_and_display_keep_existing_meaning(self):
        for visibility, display, width, height in (
                ("hidden", "block", 200, 80), ("visible", "none", 200, 80),
                ("visible", "block", 200, 80), ("visible", "block", 0, 80),
                ("visible", "block", 200, 0)):
            with self.subTest(visibility=visibility, display=display, width=width, height=height):
                fixture = cards_fixture(1)
                fixture["prepare"] += (
                    "globalThis.getComputedStyle=e=>{op('style_reads');return {visibility:'"
                    + visibility + "',display:'" + display + "'};};"
                    "cards[0].getBoundingClientRect=()=>{op('geometry_reads');return {width:"
                    + str(width) + ",height:" + str(height) + "};};"
                )
                _, new = self.equivalent(**fixture)
                self.assertEqual(bool(new["final"]["failures"]),
                                 visibility != "hidden" and display != "none" and width > 0 and height > 0)

    def test_occlusion_and_offscreen_geometry_do_not_add_new_visibility_rules(self):
        fixture = cards_fixture(1)
        fixture["prepare"] += """
document.elementFromPoint=()=>{op('hit_tests');return body;};
cards[0].getBoundingClientRect=()=>{op('geometry_reads');return {x:-10000,y:-10000,width:200,height:80};};
"""
        _, new = self.equivalent(**fixture)
        self.assertTrue(new["final"]["failures"])
        self.assertEqual(new["operations"].get("hit_tests", 0), 0)

    def test_connectivity_is_still_rechecked_after_visibility(self):
        fixture = cards_fixture(1)
        fixture["prepare"] += """
cards[0].getBoundingClientRect=()=>{
 op('geometry_reads');if(cards[0].parentElement)detach(cards[0]);return {width:200,height:80};
};
"""
        _, new = self.equivalent(**fixture)
        self.assertEqual(new["final"]["failures"], [])

    def test_remounted_history_and_new_nested_messages_keep_attribution(self):
        cases = (
            ("const old=history.append(new Element('article',exact));",
             "detach(old);add(history,new Element('article',exact));", True),
            ("const old=history.append(new Element('article',exact));",
             "detach(old);add(history,old);", False),
            ("", "const card=new Element('article');const p=card.append(new Element('p'));"
             "for(const clause of exact.split('. '))p.append(new Element('span',clause+'. '));"
             "add(history,card);", False),
        )
        for setup, actions, ambiguous in cases:
            with self.subTest(ambiguous=ambiguous):
                _, new = self.equivalent(setup=setup, actions=actions)
                self.assertEqual(new["final"]["ambiguous"], ambiguous)

    def test_exact_unknown_and_incomplete_failures_match_original(self):
        for message in (POLICY_FAILURE, HIGH_DEMAND_FAILURE, UNUSUAL_ACTIVITY_FAILURE,
                        UNUSUAL_ACTIVITY_EXTENSION_FAILURE, GENERIC_GENERATION_FAILURE,
                        "You've reached your usage limit.", "Generation failed SYNTHETIC_CANARY", "Failed"):
            for debug in (False, True):
                with self.subTest(debug=debug, first_word=message.split()[0]):
                    self.equivalent(failure=message, debug=debug,
                                    actions="add(history,new Element('p',exact));take();")

    def test_cleanup_rearm_and_replacement_do_not_reuse_visibility(self):
        self.equivalent(setup="const p=history.append(new Element('p'));", actions="""
change(p,exact);p.hidden=true;compiled[2](true);take();
compiled[4]();install();compiled[2]();state=window.__nanoBatchCurrentGenerationAttempt;
p.hidden=false;state.observer.callback([{type:'attributes',target:p,attributeName:'style',oldValue:''}]);take();
""")


if __name__ == "__main__":
    unittest.main()

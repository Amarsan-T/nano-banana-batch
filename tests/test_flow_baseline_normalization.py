"""Synthetic differential checks against the pre-optimization text normalizer.

No browser, network, profile, or real run is used. Timing is opt-in through
benchmark(); unit tests assert work counts and semantics, never timing thresholds.
"""

import json
import shutil
import statistics
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nano_batch.adapters.flow import FlowSession, _safe_current_generation_failure
from tests.test_flow_explicit_failures import (
    GENERIC_GENERATION_FAILURE, HIGH_DEMAND_FAILURE, POLICY_FAILURE, PROJECT_URL,
    UNUSUAL_ACTIVITY_FAILURE, UNUSUAL_ACTIVITY_EXTENSION_FAILURE,
    _generation_failure_observer_scripts,
)
from tests.test_flow_observer_text_reuse import _HARNESS


# Frozen from 06ace77: retain callback-local reuse and all three full text reads.
_ORIGINAL_NORMALIZER = r"""const normalizedTexts = (element) => {
                const cached = callbackTexts?.get(element);
                if (cached) return cached;
                const values = readTiming ? [
                  readStep('aria_label_read', () => element.getAttribute('aria-label')),
                  readStep('inner_text_read', () => element.innerText),
                  readStep('text_content_read', () => element.textContent),
                ] : [
                  element.getAttribute('aria-label'),
                  element.innerText,
                  element.textContent,
                ];
                const unique = [];
                for (const [index, value] of values.entries()) {
                  const text = readTiming ? readStep('normalization', () =>
                    String(value || '').replace(/\s+/g, ' ').trim()) :
                    String(value || '').replace(/\s+/g, ' ').trim();
                  // A label is its own message channel, never descendant text.
                  if (index === 0) unique.ariaLabel = text.length <= 1200 ? text : '';
                  if (text && text.length <= 1200 && !unique.includes(text)) {
                    unique.push(text);
                  }
                }
                callbackTexts?.set(element, unique);
                return unique;
              };
              """


def original_begin(begin):
    start = begin.index("const normalizedTexts = (element")
    end = begin.index("const failurePattern =", start)
    begin = begin[:start] + _ORIGINAL_NORMALIZER + begin[end:]
    return begin.replace("const texts = normalizedTexts(element, true);\n                  const signature",
                         "const texts = normalizedTexts(element);\n                  const signature")


_COUNTERS = r"""
const uncountedInstrument = instrument;
instrument = script => uncountedInstrument(script)
  .replaceAll("String(value || '').slice(0, 2400).replace(/\\s+/g, ' ').trim()",
              "countChannel(String(value || '').slice(0, 2400))")
  .replaceAll("String(value || '').replace(/\\s+/g, ' ').trim()",
              "countChannel(String(value || ''))")
  .replace('const unique = [];', 'noteValues(values); const unique = [];');
globalThis.countChannel = raw => {
  op('channel_normalizations'); op('normalization_characters', raw.length);
  const text = raw.replace(/\s+/g, ' ').trim();
  if (metrics) metrics.peak_normalized_characters = Math.max(
    metrics.peak_normalized_characters || 0, text.length);
  return text;
};
globalThis.noteValues = values => {
  if (metrics) metrics.peak_raw_characters = Math.max(metrics.peak_raw_characters || 0,
    values.reduce((sum, value) => sum + String(value || '').length, 0));
};
"""


def probe(*, setup="", prepare="", actions="", baseline=False, benchmark=False,
          debug=False, failure=GENERIC_GENERATION_FAILURE):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for offline observer tests.")
    begin, arm, poll = _generation_failure_observer_scripts()
    session = FlowSession(PROJECT_URL, Path("unused-synthetic-profile"))
    session.page = MagicMock()
    session._stop_current_generation_attempt_observation()
    stop = session.page.evaluate.call_args.args[0]
    data = dict(scripts=[original_begin(begin), begin, arm, poll, stop],
                failure=failure, debug=debug, benchmark=benchmark)
    harness = _HARNESS.replace("INPUT", json.dumps(data))
    harness = harness.replace("const compiled =", _COUNTERS + "\nconst compiled =", 1)
    harness = harness.replace("const facts={", """const facts={
      diagnosticKeys:Object.keys(d).sort(),
      serializedDiagnostics:JSON.stringify(d),""", 1)
    if not benchmark:
        # Equal diagnostic timestamps/durations as well as equal stage call counts.
        # Performance runs below retain the real clock and uninstrumented scripts.
        harness = harness.replace("const {performance: clock} = require('node:perf_hooks');",
                                  "const {performance: clock} = require('node:perf_hooks'); let syntheticClock=100;")
        harness = harness.replace("return clock.now();}};", "return ++syntheticClock;}};")
        harness = harness.replace("function run(reference, measured=true) {",
                                  "function run(reference, measured=true) { syntheticClock=100;")
    if baseline:
        harness = harness.replace("const history=body.append(new Element('section'));",
                                  "const history=body;")
        harness = harness.replace("  compiled[2]();\n", "", 1)
        actions = "compiled[2]();\n" + actions
    harness = harness.replace("SETUP", setup).replace("PREPARE", prepare).replace("ACTIONS", actions)
    if not benchmark:
        harness = harness[:harness.index("if(input.benchmark) {")] + """
console.log(JSON.stringify({before:run(true),after:run(false)}));
"""
    completed = subprocess.run([node, "-"], input=harness, text=True, encoding="utf-8",
                               capture_output=True, timeout=120, check=True)
    return json.loads(completed.stdout)


def fixture(name, *, cached=False):
    setup = {
        "flat10000": "for(let i=0;i<10000;i++)history.append(new Element('div',ordinary));",
        "deep2000": "let p=history;for(let i=0;i<2000;i++)p=p.append(new Element('div',ordinary));",
        "deep4000": "let p=history;for(let i=0;i<4000;i++)p=p.append(new Element('div',ordinary));",
        "history1000": "for(let i=0;i<1000;i++)history.append(new Element('article')).append(new Element('p',exact));",
        "nested500": "for(let i=0;i<500;i++){const m=history.append(new Element('article')).append(new Element('p'));for(const c of exact.split('. '))m.append(new Element('span',c+'. '));}",
        "aria1000": "for(let i=0;i<1000;i++)history.append(new Element('article','x'.repeat(2000),{'aria-label':exact}));",
        "overlap": "let p=history;for(let i=0;i<200;i++)p=p.append(new Element('section'));for(let i=0;i<1000;i++)p.append(new Element('span',ordinary));",
        "whitespace2000": "let p=history;for(let i=0;i<2000;i++)p=p.append(new Element('div',' \\t\\u00a0 '));",
    }[name]
    return dict(baseline=True, setup="const ordinary='synthetic ordinary text content  ';\n" +
                setup + ("\ncacheTree(body);" if cached else ""))


def benchmark():
    """Return repeatable synthetic before/after evidence, without writing files."""
    rows = []
    for name, cached in (
        ("flat10000", True), ("deep2000", True), ("deep4000", True),
        ("deep2000", False), ("deep4000", False), ("history1000", False),
        ("nested500", False), ("aria1000", False), ("overlap", True),
        ("whitespace2000", True),
    ):
        result = probe(benchmark=True, **fixture(name, cached=cached))
        if result["before"]["snapshots"] != result["after"]["snapshots"]:
            raise AssertionError("Benchmark attribution mismatch.")
        rows.append(dict(fixture=name, cached=cached,
                         times_ms=result["times"], node=result["node"],
                         median_ms={key: statistics.median(value)
                                    for key, value in result["times"].items()},
                         before=result["before"]["operations"],
                         after=result["after"]["operations"]))
    return rows


class BaselineNormalizationTests(unittest.TestCase):
    def equivalent(self, **kwargs):
        result = probe(**kwargs)
        old, new = result["before"], result["after"]
        self.assertEqual(new["snapshots"], old["snapshots"])
        for key in ("requests", "computations", "unique_elements", "innerText_reads",
                    "textContent_reads", "aggregate_characters", "text_node_visits",
                    "style_reads", "geometry_reads", "selector_calls", "selector_visits"):
            self.assertEqual(new["operations"].get(key, 0), old["operations"].get(key, 0))
        return old, new

    def test_deep_baseline_avoids_rejected_full_string_normalization(self):
        old, new = self.equivalent(baseline=True, setup="""
let p=history;for(let i=0;i<600;i++)p=p.append(new Element('div','synthetic ordinary text content  '));
cacheTree(body);
""")
        self.assertEqual(new["operations"]["unique_elements"], 600)
        self.assertLess(new["operations"]["normalization_characters"],
                        old["operations"]["normalization_characters"] / 4)
        self.assertEqual(new["cacheMetrics"], [])

    def test_overlapping_ancestors_avoid_redundant_normalization(self):
        old, new = self.equivalent(**fixture("overlap", cached=True))
        self.assertLess(new["operations"]["normalization_characters"],
                        old["operations"]["normalization_characters"] / 4)

    def test_flat_baseline_preserves_all_nodes_channels_and_work(self):
        old, new = self.equivalent(**fixture("flat10000"))
        self.assertEqual(new["operations"], old["operations"])
        self.assertEqual(new["operations"]["unique_elements"], 10000)
        self.assertFalse(new["final"]["baselineTruncated"])

    def test_normalization_boundaries_and_raw_read_order(self):
        # Export only inside the synthetic harness; production has no test API.
        begin, arm, poll = _generation_failure_observer_scripts()
        exposed = [original_begin(begin), begin]
        prefix = _HARNESS.split("function instrument(script)")[0]
        script = prefix.replace("INPUT", json.dumps(dict(scripts=exposed))) + r"""
const methods=input.scripts.map(script=>{
  elements=[];active=new Set();globalThis.window={};const body=new Element('body');
  globalThis.document={body,createTreeWalker:walker};
  script=script.replace('state.hasFailureProvenance = () =>',
    'state.testNormalize = e => normalizedTexts(e, true); state.hasFailureProvenance = () =>');
  (0,eval)('('+script+')')(false);return window.__nanoBatchCurrentGenerationAttempt.testNormalize;
});
let cases=0;
const check=values=>{
  const results=methods.map(fn=>{const text=fn({getAttribute:()=>values[0],innerText:values[1],textContent:values[2]});return JSON.stringify({values:[...text],aria:text.ariaLabel});});
  if(results[0]!==results[1])throw new Error('Synthetic channel mismatch at '+cases);
  cases++;
};
const units=['x',' ','\t','\n','\r\n','\u00a0','\ufeff','\u2028','\u2029','\u200b','\ud83d\ude00','e\u0301'];
for(const n of [0,1,1199,1200,1201,2399,2400,2401,9000])for(const unit of units){
  const raw=unit.repeat(n);
  for(const v of [[null,raw,raw],[raw,'x',raw],['',raw+'failure',raw+'\n'],
      [raw,' '.repeat(6000)+'Failed. '.repeat(5),'x'.repeat(1200)],
      ['',raw+'a'+' '.repeat(6000),raw+'a'+' '.repeat(6000)]])check(v);
}
let seed=0x71a25;
const next=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed;};
for(let i=0;i<5000;i++){
  const parts=[];for(let j=0;j<20;j++)parts.push(units[next()%units.length].repeat(next()%300));
  const raw=parts.join('');check([i%3?null:raw,raw,i%2?raw:raw+'\n']);
}
// Split a surrogate pair exactly at the raw-prefix boundary.
check(['','x'.repeat(2399)+'\ud83d\ude00','x'.repeat(2399)+'\ud83d\ude00']);
check(['',' '.repeat(2399)+'\ud83d\ude00',' '.repeat(2399)+'\ud83d\ude00']);
for(const fn of methods){
  const order=[];let content='ordinary';
  fn({getAttribute(){order.push('aria');return 'Failed.';},
      get innerText(){order.push('inner');content='Generation failed';return 'old text';},
      get textContent(){order.push('content');return content;}});
  if(order.join(',')!=='aria,inner,content')throw new Error('Raw getter order changed');
}
console.log(JSON.stringify({cases,equivalent:true}));
"""
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                encoding="utf-8", capture_output=True, check=True, timeout=30)
        self.assertEqual(json.loads(result.stdout), {"cases": 5542, "equivalent": True})

    def test_whitespace_and_accepted_limit_evidence_are_not_truncated(self):
        for text in (" " * 9000 + GENERIC_GENERATION_FAILURE + "\t" * 9000,
                     "x" * 1107 + " " + GENERIC_GENERATION_FAILURE,
                     "\u00a0" * 2400 + GENERIC_GENERATION_FAILURE):
            with self.subTest(kind=len(text)):
                _, new = self.equivalent(baseline=True,
                    setup="history.append(new Element('p'," + json.dumps(text) + "));",
                    actions="const c=history.children[0];deliver([{type:'attributes',target:c,attributeName:'class',oldValue:''}]);")
                self.assertTrue(new["final"]["historical"])
                self.assertEqual(new["final"]["failures"], [])

    def test_independent_label_survives_oversized_descendant_text(self):
        _, new = self.equivalent(setup="const m=history.append(new Element('article','x'.repeat(10000),{'aria-label':exact}));",
                                actions="detach(m);add(history,new Element('article','x'.repeat(10000),{'aria-label':exact}));")
        self.assertTrue(new["final"]["ambiguous"])
        self.assertEqual(new["final"]["failures"], [])

    def test_nested_hidden_remounted_history_and_qualified_transitions(self):
        scenarios = (
            ("const old=history.append(new Element('article',exact));old.hidden=true;",
             "old.hidden=false;deliver([{type:'attributes',target:old,attributeName:'style',oldValue:''}]);", False),
            ("const old=history.append(new Element('article'));old.append(new Element('p',exact));",
             "detach(old);const c=new Element('article');c.append(new Element('p',exact));add(history,c);", True),
            ("const old=history.append(new Element('p',exact));", "detach(old);add(history,old);", False),
            ("const old=history.append(new Element('p',exact));", "change(old,'Rendering');take();change(old,exact);", False),
            ("history.append(new Element('article',exact));",
             "const c=add(history,new Element('article'));const p=add(c,new Element('div','Rendering',{role:'progressbar'}));take();detach(p);add(c,new Element('p',exact));", False),
        )
        for debug in (False, True):
            for setup, actions, ambiguous in scenarios:
                with self.subTest(debug=debug, actions=actions):
                    _, new = self.equivalent(setup=setup, actions=actions, debug=debug)
                    self.assertEqual(new["final"]["ambiguous"], ambiguous)

    def test_all_failure_classes_and_unknown_privacy_are_preserved(self):
        for message in (GENERIC_GENERATION_FAILURE, POLICY_FAILURE, HIGH_DEMAND_FAILURE,
                        UNUSUAL_ACTIVITY_FAILURE, UNUSUAL_ACTIVITY_EXTENSION_FAILURE,
                        "You've reached your usage limit.", "Generation failed SYNTHETIC_PRIVATE_CANARY"):
            with self.subTest(message_class=message.split()[0]):
                _, new = self.equivalent(failure=message, actions="add(history,new Element('p',exact));")
                self.assertTrue(new["final"]["failures"])
                safe = json.dumps([_safe_current_generation_failure(t) for t in new["final"]["failures"]])
                self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", safe)

    def test_existing_attempt_rearm_preserves_baseline_and_provenance(self):
        _, new = self.equivalent(setup="const m=history.append(new Element('p'));",
                                actions="change(m,'Failed');take();state.active=false;compiled[2](true);change(m,exact);")
        self.assertEqual(new["final"]["failures"], [GENERIC_GENERATION_FAILURE])

    def test_preserved_rearm_does_not_repeat_baseline_work(self):
        self.equivalent(baseline=True, setup="history.append(new Element('p','x'.repeat(10000)));",
                        actions="""
take();const work=metrics.normalization_characters, boundary=state.activatedAtMs;
state.active=false;compiled[2](true);
if(metrics.normalization_characters!==work || state.activatedAtMs!==boundary)
  throw new Error('Preserved rearm repeated or replaced the baseline');
""")

    def test_baseline_and_retention_overflow_remain_ambiguous(self):
        for setup, actions in (
            ("for(let i=0;i<10001;i++)history.append(new Element('div'));", "add(history,new Element('p',exact));"),
            ("", "for(let i=0;i<201;i++)add(history,new Element('p',exact));"),
        ):
            with self.subTest(kind=bool(setup)):
                _, new = self.equivalent(setup=setup, actions=actions)
                self.assertTrue(new["final"]["ambiguous"])

    def test_callback_and_poll_normalization_work_is_unchanged(self):
        for actions in ("state.observer.callback(records);", "state.trackFailureContainers(root,true);take();"):
            with self.subTest(actions=actions):
                old, new = self.equivalent(prepare="""
const root=new Element('div');let p=root;
for(let i=0;i<100;i++)p=p.append(new Element('div','synthetic ordinary text content  '));
history.append(root);cacheTree(body);
const records=[{type:'childList',target:history,addedNodes:[root],removedNodes:[]}];
""", actions=actions)
                for key in ("channel_normalizations", "normalization_characters", "peak_normalized_characters"):
                    self.assertEqual(new["operations"][key], old["operations"][key])


if __name__ == "__main__":
    unittest.main()

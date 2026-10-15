"""Offline differential and operation-count checks for callback-local text reuse.

The reference freezes the two affected JavaScript blocks from 37d9a03.
No browser, network, profile, or production runtime data is used.
"""

import json
import shutil
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

_REFERENCE_NORMALIZER = r"""const normalizedTexts = (element) => {
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
                return unique;
              };
              """
_REFERENCE_CALLBACK = r"""state.observer = new MutationObserver((records) => {
                if (!state.active) return;
                const callbackStarted = diagnosticEnabled ? performance.now() : null;
                if (callbackStarted !== null && state.firstMutationAtMs === null) {
                  state.firstMutationAtMs = callbackStarted;
                }
                state.mutationCount = Math.min(100000, state.mutationCount + records.length);
                state.postStartFailureMutationCount = Math.min(
                  100000, state.postStartFailureMutationCount + records.length
                );
                state.lastMutationAtMs = performance.now();
                for (const record of records) {
                  if (record.type === 'childList') {
                    remember(record.target);
                    for (const node of record.addedNodes) inspectAddedSubtree(node);
                  } else if (record.type === 'characterData') {
                    const changed = evidence([record.target.textContent || '']) !==
                      evidence([record.oldValue || '']);
                    remember(record.target.parentElement, changed);
                  } else if (record.type === 'attributes') {
                    const changed = record.attributeName === 'aria-label' &&
                      evidence([record.target.getAttribute('aria-label') || '']) !==
                      evidence([record.oldValue || '']);
                    remember(record.target, changed);
                  }
                }
                if (callbackStarted !== null) {
                  const elapsed = Math.max(0, performance.now() - callbackStarted);
                  state.callbackCount = Math.min(1000000000, state.callbackCount + 1);
                  state.callbackTotalMs = Math.min(1000000000, state.callbackTotalMs + elapsed);
                  state.callbackMaxMs = Math.max(state.callbackMaxMs, elapsed);
                }
              });
              """


def reference_begin(begin):
    """Replace only the changed blocks; all attribution logic stays shared."""
    start = begin.index("const normalizedTexts = (element")
    end = begin.index("const failurePattern =", start)
    begin = begin[:start] + _REFERENCE_NORMALIZER + begin[end:]
    start = begin.index("state.observer = new MutationObserver((records) => {")
    end = begin.index("state.observer.observe(document.body", start)
    return begin[:start] + _REFERENCE_CALLBACK + begin[end:]


_HARNESS = r"""
const input = INPUT;
const {performance: clock} = require('node:perf_hooks');
let metrics = null, unique = null, cacheMetrics = [], elements = [], active = new Set();
const op = (key, value=1) => {if (metrics) metrics[key]=(metrics[key]||0)+value;};
globalThis.noteNormalization = e => {
  op('computations');
  if (unique && !unique.has(e)) {unique.add(e);op('unique_elements');}
};
globalThis.normalizationRequest = () => op('requests');
class ProbeTextCache extends WeakMap {
  constructor() {super();this.metric={entries:0,characters:0,hits:0,filtered:true};cacheMetrics.push(this.metric);}
  get(key) {const value=super.get(key);if(value)this.metric.hits++;return value;}
  set(key,value) {
    if(!this.has(key)) {
      this.metric.entries++;
      this.metric.characters+=value.reduce((sum,text)=>sum+text.length,0)+value.ariaLabel.length;
    }
    this.metric.filtered &&= Array.isArray(value) &&
      value.every(t=>typeof t==='string' && t.length<=1200) &&
      typeof value.ariaLabel==='string' && value.ariaLabel.length<=1200;
    return super.set(key,value);
  }
}
globalThis.ProbeTextCache=ProbeTextCache;
const selectors=new Map();
function matcher(selector) {
  if(selectors.has(selector))return selectors.get(selector);
  const rules=selector.split(',').map(s=>s.trim()).map(s=>{
    const a=/^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(s);
    return a ? e=>a[2]===undefined ? e.attributes[a[1]]!==undefined : e.attributes[a[1]]===a[2]
      : e=>e.tagName.toLowerCase()===s;
  });
  const result=e=>rules.some(test=>test(e));selectors.set(selector,result);return result;
}
class Element {
  constructor(tag='div',text='',attributes={}) {
    this.tagName=tag.toUpperCase();this.ownText=text;this.attributes=attributes;
    this.parentElement=null;this.children=[];this.hidden=false;this.cached=null;
    this.id=elements.length;elements.push(this);
  }
  append(child) {child.parentElement=this;this.children.push(child);return child;}
  get isConnected() {
    op('connected_reads');let e=this;while(e.parentElement)e=e.parentElement;
    return e.tagName==='BODY';
  }
  aggregate(channel) {
    op(channel+'_reads');
    if(this.throwText)throw new Error('synthetic text getter failure');
    let result;
    if(this.cached)result=this.cached[channel];
    else {
      const parts=[],stack=[this];let visits=0;
      while(stack.length) {
        const e=stack.pop();visits++;if(e.ownText)parts.push(e.ownText);
        for(let i=e.children.length-1;i>=0;i--)stack.push(e.children[i]);
      }
      op('text_node_visits',visits);
      result=parts.join(channel==='innerText'?'\n':'');
    }
    op('aggregate_characters',result.length);return result;
  }
  get innerText(){return this.aggregate('innerText');}
  get textContent(){return this.aggregate('textContent');}
  getAttribute(name){return this.attributes[name]??null;}
  matches(selector){return matcher(selector)(this);}
  querySelectorAll(selector) {
    op('selector_calls');const found=[],stack=[...this.children].reverse(),test=matcher(selector);
    while(stack.length) {
      const e=stack.pop();op('selector_visits');if(test(e))found.push(e);
      for(let i=e.children.length-1;i>=0;i--)stack.push(e.children[i]);
    }
    return found;
  }
  contains(element){for(let e=element;e;e=e.parentElement)if(e===this)return true;return false;}
  getBoundingClientRect(){op('geometry_reads');return {width:this.hidden?0:200,height:80};}
}
class MutationObserver {
  constructor(callback){this.callback=callback;}
  observe(){active.add(this);}
  disconnect(){active.delete(this);}
  takeRecords(){return [];}
}
globalThis.Element=Element;globalThis.MutationObserver=MutationObserver;
globalThis.getComputedStyle=e=>{op('style_reads');return {visibility:'visible',display:'block'};};
globalThis.performance={now:()=>{op('clock_reads');return clock.now();}};
globalThis.setTimeout=()=>1;globalThis.clearTimeout=()=>{};
function walker(root) {
  const stack=[...root.children].reverse();
  return {nextNode(){
    if(!stack.length)return null;const e=stack.pop();
    for(let i=e.children.length-1;i>=0;i--)stack.push(e.children[i]);return e;
  }};
}
function cacheTree(root) {
  const order=[],stack=[root];
  while(stack.length){const e=stack.pop();order.push(e);stack.push(...e.children);}
  for(let i=order.length-1;i>=0;i--){
    const e=order[i];e.cached={
      innerText:[e.ownText,...e.children.map(c=>c.cached.innerText)].filter(Boolean).join('\n'),
      textContent:e.ownText+e.children.map(c=>c.cached.textContent).join('')
    };
  }
}
function instrument(script) {
  const cached=script.includes('let callbackTexts = null;');
  return script
    .replace(/const normalizedTexts = \(element(?:, baselineCapture = false)?\) => \{/,
      '$& normalizationRequest();')
    .replace('const values = readTiming ? [','noteNormalization(element); const values = readTiming ? [')
    .replace('callbackTexts = new WeakMap();','callbackTexts = new ProbeTextCache();')
    .replace('state.observer = new MutationObserver',
      'state.__cacheActive = () => '+(cached?'callbackTexts !== null':'false')+
      '; state.observer = new MutationObserver');
}
const compiled = input.scripts.map(s=>(0,eval)('('+s+')'));
function run(reference, measured=true) {
  metrics=null;unique=null;cacheMetrics=[];elements=[];active=new Set();globalThis.window={};
  const body=new Element('body');
  globalThis.document={body,createTreeWalker:walker,addEventListener(){},removeEventListener(){}};
  const history=body.append(new Element('section'));
  const exact=input.failure, snapshots=[], samples=[];
  SETUP
  const script=input.scripts[reference?0:1];
  const install=()=>
    (measured?(0,eval)('('+instrument(script)+')'):compiled[reference?0:1])(input.debug);
  install();
  compiled[2]();
  let state=window.__nanoBatchCurrentGenerationAttempt;
  const take=()=>{
    const raw=compiled[3](input.debug),d=raw.diagnostics;
    const facts={
      failures:raw.failures,ambiguous:raw.failure_attribution_ambiguous,
      reason:d.last_failure_detection_reason,mutations:d.generation_dom_mutation_count,
      visible:d.visible_failure_container_count,current:d.current_attempt_failure_container_count,
      retained:state.failureContainers.map(e=>e.id),baselineTruncated:state.failureBaselineTruncated,
      containerTruncated:state.failureContainerTruncated,
      historical:[...state.historicalFailureEvidence].sort(),
      addedRoots:elements.map(e=>state.addedRoots.has(e)),
      progressAncestors:elements.map(e=>state.progressAncestors.has(e)),
      progressNodes:elements.map(e=>state.progressNodes.has(e)),
      progressCount:state.progressRegionCount,progressTruncated:state.progressTruncated,
      addedCount:state.addedRootCount,addedTruncated:state.addedRootTruncated,
      callbackCount:state.callbackCount,postStartMutations:state.postStartFailureMutationCount,
      unusualMatches:state.unusualSignatureMatchCount,genericMatches:state.genericSignatureMatchCount,
      sources:elements.map(e=>state.failureMutationSources.get(e)?.id??null),
      baseline:elements.map(e=>state.failureBaseline.get(e)??null),
      progress:elements.map(e=>state.progressOwners.has(e)),
      cleared:elements.map(e=>state.clearedFailureSurfaces.has(e))
    };
    snapshots.push(facts);return facts;
  };
  const deliver=records=>{
    const prior=metrics;metrics={};unique=new WeakSet();
    try {state.observer.callback(records);}
    finally {samples.push(metrics);metrics=prior;unique=null;}
  };
  const add=(parent,child)=>{
    parent.append(child);deliver([{type:'childList',target:parent,addedNodes:[child],removedNodes:[]}]);
    return child;
  };
  const change=(element,text)=>{
    const oldValue=element.ownText;element.ownText=text;
    deliver([{type:'characterData',target:{parentElement:element,textContent:text},oldValue}]);
  };
  const label=(element,text)=>{
    const oldValue=element.getAttribute('aria-label');element.attributes['aria-label']=text;
    deliver([{type:'attributes',target:element,attributeName:'aria-label',oldValue}]);
  };
  const detach=element=>{
    const parent=element.parentElement;parent.children=parent.children.filter(e=>e!==element);element.parentElement=null;
  };
  PREPARE
  metrics=measured?{}:null;unique=measured?new WeakSet():null;
  const started=clock.now();
  ACTIONS
  const elapsed=clock.now()-started, operations=metrics;
  metrics=null;unique=null;
  const cacheActive=measured?state.__cacheActive():null;
  const final=take();
  return {snapshots,samples,final,operations,cacheActive,cacheMetrics,active:active.size,elapsed_ms:elapsed};
}
if(input.benchmark) {
  // Warm both versions, then alternate execution order to reduce systematic bias.
  run(true,false);run(false,false);
  const times={before:[],after:[]};
  for(let i=0;i<3;i++)for(const reference of i%2?[false,true]:[true,false])
    times[reference?'before':'after'].push(run(reference,false).elapsed_ms);
  const before=run(true),after=run(false);
  console.log(JSON.stringify({times,before,after,node:process.version}));
}else console.log(JSON.stringify(run(input.reference)));
"""


def probe(*, setup="", prepare="", actions="", reference=False, debug=False,
          failure=GENERIC_GENERATION_FAILURE, benchmark=False):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for offline observer tests.")
    begin, arm, poll = _generation_failure_observer_scripts()
    session = FlowSession(PROJECT_URL, Path("unused-synthetic-profile"))
    session.page = MagicMock()
    session._stop_current_generation_attempt_observation()
    stop = session.page.evaluate.call_args.args[0]
    data = dict(scripts=[reference_begin(begin), begin, arm, poll, stop],
                reference=reference, debug=debug, failure=failure, benchmark=benchmark)
    script = _HARNESS.replace("INPUT", json.dumps(data)).replace("SETUP", setup)
    script = script.replace("PREPARE", prepare).replace("ACTIONS", actions)
    completed = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8",
                               capture_output=True, timeout=120, check=True)
    return json.loads(completed.stdout)


def bulk_fixture(count, *, deep=False, overlap=False):
    prepare = """
const root=new Element('div');let parent=root;
for(let i=0;i<COUNT;i++){
  const node=new Element('div','synthetic ordinary text content  ');
  (DEEP?parent:root).append(node);parent=node;
}
body.append(root);cacheTree(body);
const records=[{type:'childList',target:body,addedNodes:[root],removedNodes:[]}];
if(OVERLAP)records.push({type:'childList',target:root,addedNodes:[root.children[0]],removedNodes:[]});
""".replace("COUNT", str(count)).replace("DEEP", json.dumps(deep)).replace("OVERLAP", json.dumps(overlap))
    return dict(prepare=prepare, actions="state.observer.callback(records);")


class CallbackTextReuseTests(unittest.TestCase):
    def equivalent(self, **kwargs):
        old, new = probe(reference=True, **kwargs), probe(**kwargs)
        self.assertEqual(new["snapshots"], old["snapshots"])
        for key in ("style_reads", "geometry_reads", "selector_calls", "selector_visits"):
            self.assertEqual(new["operations"].get(key, 0), old["operations"].get(key, 0))
        self.assertFalse(new["cacheActive"])
        self.assertTrue(all(cache["filtered"] for cache in new["cacheMetrics"]))
        return old, new

    def test_bulk_insertion_computes_once_per_element_per_callback(self):
        for deep, overlap in ((False, False), (True, False), (True, True)):
            with self.subTest(deep=deep, overlap=overlap):
                old, new = self.equivalent(**bulk_fixture(50, deep=deep, overlap=overlap))
                self.assertEqual(new["operations"]["requests"], old["operations"]["requests"])
                self.assertEqual(new["operations"]["computations"], new["operations"]["unique_elements"])
                self.assertLess(new["operations"]["computations"], old["operations"]["computations"] / 2)
                self.assertLess(new["operations"]["aggregate_characters"], old["operations"]["aggregate_characters"])
                self.assertEqual(len(new["cacheMetrics"]), 1)
                self.assertEqual(new["cacheMetrics"][0]["entries"], new["operations"]["computations"])

    def test_incremental_records_reuse_values_without_dropping_records(self):
        old, new = self.equivalent(setup="""
let node=history;for(let i=0;i<20;i++)node=node.append(new Element('div','ordinary'));
""", actions="""
const records=Array.from({length:100},()=>({type:'characterData',
 target:{parentElement:node,textContent:node.ownText},oldValue:'previous'}));
state.observer.callback(records);
""")
        self.assertEqual(new["final"]["mutations"], 100)
        self.assertEqual(new["operations"]["requests"], old["operations"]["requests"])
        self.assertEqual(new["operations"]["computations"], 12)
        self.assertEqual(old["operations"]["computations"], 1200)

    def test_fresh_values_and_independent_labels_between_callbacks(self):
        old, new = self.equivalent(setup="const card=history.append(new Element('article'));const p=card.append(new Element('p'));",
            actions="change(p,'Failed');take(); label(p,exact);take(); label(p,'');change(p,exact);")
        self.assertEqual(new["snapshots"][0]["failures"], [])
        self.assertTrue(new["final"]["failures"])
        self.assertGreaterEqual(len(new["cacheMetrics"]), 4)
        self.assertTrue(all(sample["computations"] == sample["unique_elements"] for sample in new["samples"]))

    def test_poll_reads_are_fresh_without_a_new_callback(self):
        old, new = self.equivalent(setup="const p=history.append(new Element('p'));",
            actions="change(p,'Failed');take(); p.ownText=exact;")
        self.assertEqual(new["snapshots"][0]["failures"], [])
        self.assertEqual(new["final"]["failures"], [GENERIC_GENERATION_FAILURE])
        self.assertEqual(len(new["cacheMetrics"]), 1)

    def test_exception_discards_cache_before_direct_reads_and_next_callback(self):
        old, new = self.equivalent(setup="const p=history.append(new Element('p'));const broken=history.append(new Element('p'));",
            actions="""
p.ownText='Failed';broken.throwText=true;
let caught=false;
try {deliver([
 {type:'characterData',target:{parentElement:p,textContent:p.ownText},oldValue:''},
 {type:'characterData',target:{parentElement:broken,textContent:'x'},oldValue:''}
]);} catch(error){caught=error.message==='synthetic text getter failure';}
if(!caught)throw new Error('expected getter failure');
if(state.__cacheActive())throw new Error('cache survived exceptional exit');
broken.throwText=false;p.ownText=exact;state.trackFailureContainers(p,true);take();
change(p,'ordinary');change(p,exact);
""")
        self.assertTrue(new["final"]["failures"])
        self.assertEqual(len(new["cacheMetrics"]), 3)

    def test_history_remount_and_local_transitions_remain_equivalent(self):
        scenarios = (
            ("const old=history.append(new Element('article',exact));",
             "deliver([{type:'attributes',target:old,attributeName:'class',oldValue:''}]);", False),
            ("const old=history.append(new Element('article',exact));",
             "detach(old);add(history,new Element('article',exact));", True),
            ("const old=history.append(new Element('article',exact));const card=history.append(new Element('article'));const p=card.append(new Element('p'));",
             "change(p,'Rendering');change(p,exact);", False),
            ("const old=history.append(new Element('article',exact));",
             "change(old,'Rendering');take();change(old,exact);", False),
            ("const old=history.append(new Element('article',exact));",
             "detach(old);add(history,old);", False),
            ("history.append(new Element('article',exact));",
             "const card=add(history,new Element('article'));"
             "const progress=add(card,new Element('div','Rendering',{role:'progressbar'}));"
             "take();detach(progress);add(card,new Element('p',exact));", False),
        )
        for setup, actions, ambiguous in scenarios:
            with self.subTest(actions=actions):
                old, new = self.equivalent(setup=setup, actions=actions)
                self.assertEqual(new["final"]["ambiguous"], ambiguous)

    def test_nested_messages_and_overlapping_records_preserve_sources(self):
        self.equivalent(prepare="""
const card=new Element('article');const message=card.append(new Element('div'));
for(const clause of exact.split('. '))message.append(new Element('span',clause+'. '));
history.append(card);
""", actions="""
deliver([
 {type:'childList',target:history,addedNodes:[card],removedNodes:[]},
 {type:'childList',target:message,addedNodes:[message.children[0]],removedNodes:[]},
 {type:'characterData',target:{parentElement:message.children[1],textContent:message.children[1].ownText},oldValue:''}
]);
""")

    def test_all_exact_classifications_and_unknown_text_remain_equivalent(self):
        for message in (POLICY_FAILURE, HIGH_DEMAND_FAILURE, UNUSUAL_ACTIVITY_FAILURE,
                        UNUSUAL_ACTIVITY_EXTENSION_FAILURE, GENERIC_GENERATION_FAILURE,
                        "You've reached your usage limit.", "Generation failed SYNTHETIC_PRIVATE_CANARY"):
            for debug in (False, True):
                with self.subTest(message_type=message.split()[0], debug=debug):
                    _, new = self.equivalent(failure=message, debug=debug,
                        actions="add(history,new Element('p',exact));")
                    self.assertTrue(new["final"]["failures"])
                    safe = json.dumps([_safe_current_generation_failure(t) for t in new["final"]["failures"]])
                    self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", safe)

    def test_label_channels_cannot_combine_with_partial_visible_text(self):
        _, new = self.equivalent(setup="const p=history.append(new Element('p'));",
            actions="""p.ownText='Failed. This generation might violate our policies.';
label(p,'Please try a different prompt or send feedback. You have not been charged for this generation.');
""", failure=POLICY_FAILURE)
        self.assertEqual(new["final"]["failures"], [])

    def test_hidden_revealed_and_detached_containers_are_fresh(self):
        self.equivalent(setup="const p=history.append(new Element('p'));",
            actions="""p.hidden=true;change(p,exact);take();
p.hidden=false;deliver([{type:'attributes',target:p,attributeName:'style',oldValue:''}]);take();
detach(p);take();add(history,p);
""")

    def test_baseline_rearm_cleanup_and_replacement_have_no_callback_cache(self):
        _, new = self.equivalent(setup="const p=history.append(new Element('p'));",
            actions="""change(p,'Failed');take();
if(state.__cacheActive())throw new Error('cache active before rearm');
compiled[2](true);p.ownText=exact;take();
compiled[4]();if(active.size!==0)throw new Error('cleanup did not disconnect');
install();compiled[2]();state=window.__nanoBatchCurrentGenerationAttempt;
state.observer.callback([{type:'attributes',target:p,attributeName:'class',oldValue:''}]);
""")
        self.assertEqual(new["active"], 1)
        self.assertEqual(len(new["cacheMetrics"]), 2)
        self.assertEqual(new["final"]["failures"], [])

    def test_cache_retains_only_filtered_strings_and_empty_results(self):
        _, new = self.equivalent(prepare="""const root=new Element('div');
for(let i=0;i<100;i++)root.append(new Element('div','x'.repeat(50)));
history.append(root);""",
            actions="deliver([{type:'childList',target:history,addedNodes:[root],removedNodes:[]}]);")
        self.assertTrue(new["cacheMetrics"])
        self.assertTrue(all(cache["filtered"] for cache in new["cacheMetrics"]))
        self.assertGreater(new["cacheMetrics"][0]["hits"], 0)

    def test_baseline_does_not_create_a_callback_cache(self):
        result = probe(setup="history.append(new Element('p',exact));")
        self.assertEqual(result["cacheMetrics"], [])
        self.assertFalse(result["cacheActive"])
        self.assertEqual(result["final"]["failures"], [])


if __name__ == "__main__":
    unittest.main()

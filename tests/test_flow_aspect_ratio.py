"""Synthetic DOM preflight checks; no browser, network, or private run data."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nano_batch.adapters.flow import FlowAutomationError, FlowSession
from nano_batch.models import GenerationSettings


PROJECT_URL = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"


def inspection_scripts():
    session = FlowSession(PROJECT_URL, Path("profile"))
    session.page = MagicMock()
    session.page.url = PROJECT_URL
    session.page.title.return_value = "Flow"
    session.page.evaluate.return_value = {
        "controls": [], "editables": [], "diagnosticText": [],
        "media": [], "iframes": [],
    }
    session.inspect_environment(stabilize=False)
    return [call.args[0] for call in session.page.evaluate.call_args_list]


DOM_FIXTURE = r"""
const fs = require('fs');
const {scripts, scenario} = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag, attrs = {}, text = '', children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = attrs; this.text = text;
    this.children = children; this.parentElement = null; this.isConnected = attrs.connected !== false;
    this.disabled = Boolean(attrs.disabled); this.className = attrs.class || '';
    for (const child of children) child.parentElement = this;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(' '); }
  get innerText() {
    return this.hidden ? '' : this.text + this.children.map(c => c.innerText).join(' ');
  }
  get hidden() { return Boolean(this.attrs.hidden) || Boolean(this.parentElement?.hidden); }
  getBoundingClientRect() {
    return {x: 10, y: 10, width: this.hidden ? 0 : 100, height: this.hidden ? 0 : 30};
  }
  contains(node) { return this === node || this.children.some(c => c.contains(node)); }
  matches(selector) {
    let negate = null;
    selector = selector.replace(/:not\(([^)]+)\)/g, (_, value) => { negate = value; return ''; });
    if (negate && this.matches(negate)) return false;
    const tag = selector.match(/^[a-z][a-z0-9-]*/i)?.[0];
    if (tag && tag.toUpperCase() !== this.tagName) return false;
    for (const match of selector.matchAll(/\[([\w-]+)(?:(\*=|=)"([^"]*)"( i)?)?\]/g)) {
      const value = this.getAttribute(match[1]);
      if (value === null) return false;
      let actual = String(value), expected = match[3];
      if (match[4]) { actual = actual.toLowerCase(); expected = expected.toLowerCase(); }
      if (match[2] === '=' && actual !== expected) return false;
      if (match[2] === '*=' && !actual.includes(expected)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (selector.split(',').some(s => child.matches(s.trim()))) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) {
      if (selector.split(',').some(s => node.matches(s.trim()))) return node;
    }
    return null;
  }
}
global.Element = Element;
global.window = {};
global.getComputedStyle = element => ({
  visibility: element.hidden ? 'hidden' : 'visible',
  display: element.hidden ? 'none' : 'block', opacity: '1', pointerEvents: 'auto',
});
const make = spec => new Element(spec.tag || 'button', spec.attrs || {}, spec.text || '',
  (spec.children || []).map(make));
const prompt = new Element('div', {contenteditable: 'true', role: 'textbox',
  'aria-label': 'Prompt'}, scenario.prompt || '');
const start = new Element('button', {'aria-label': 'Start generation', disabled: scenario.disabledStart});
const model = new Element('button', {}, 'Nano Banana 2.1 x1');
const composer = new Element(scenario.composerTag || 'form', {'aria-label': 'Prompt composer'}, '',
  [prompt, model, ...(scenario.settings || []).map(make), start]);
const header = new Element('header', {}, '', [new Element('button', {}, scenario.title ?? '02:29')]);
const history = new Element('div', {}, '', (scenario.history || []).map(make));
const body = new Element('body', {}, '', [header, history, composer,
  new Element('button', {role: 'tab', 'aria-selected': 'true'}, 'Image')]);
const html = new Element('html', {}, '', [body]);
global.document = {
  body, documentElement: html, readyState: 'complete',
  querySelectorAll: selector => html.querySelectorAll(selector),
  getElementById: id => html.querySelectorAll('[id]').find(e => e.getAttribute('id') === id),
};
const raw = eval('(' + scripts[0] + ')')();
const resolution = eval('(' + scripts[1] + ')')({includeAspectRatio: scenario.includeAspectRatio ?? true});
process.stdout.write(JSON.stringify({raw, resolution}));
"""


class AspectRatioPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise RuntimeError("Node.js is required for the offline DOM regression checks")
        cls.scripts = inspection_scripts()

    def inspect(self, settings, **scenario):
        result = subprocess.run(
            [self.node, "-e", DOM_FIXTURE],
            input=json.dumps({"scripts": self.scripts, "scenario": {
                "settings": settings, **scenario,
            }}), text=True, capture_output=True, timeout=15, check=True,
        )
        evidence = json.loads(result.stdout)
        session = FlowSession(PROJECT_URL, Path("profile"))
        page = session.page = MagicMock()
        page.url = PROJECT_URL
        page.title.return_value = "Flow"
        page.evaluate.side_effect = [evidence["raw"], evidence["resolution"]]
        inspection = session.inspect_environment(stabilize=False)
        for action in (page.click, page.fill, page.press, page.mouse.down, page.goto,
                       page.screenshot, page.context.new_cdp_session):
            action.assert_not_called()
        return session, inspection

    def configure(self, settings, **scenario):
        session, inspection = self.inspect(settings, **scenario)
        return session.configure_generation(GenerationSettings(variants=1), inspection=inspection)

    def test_reported_title_and_crop_icon_resolve_to_current_16_9(self):
        result = self.configure([{"children": [{"tag": "span", "text": "crop_16_9"}]}])
        self.assertEqual(result["aspect_ratio"], "crop_16_9")
        self.assertEqual(result["model"], "Nano Banana 2.1")
        self.assertEqual(result["configured_variants"], 1)

    def test_plain_current_ratio_excludes_title_history_and_prompt_text(self):
        session, inspection = self.inspect(
            [{"attrs": {"role": "combobox", "aria-label": "Aspect ratio 16:9"}}],
            history=[{"text": "9:16"}, {"text": "crop_1_1"}], prompt="1:1",
        )
        result = session.configure_generation(GenerationSettings(variants=1), inspection=inspection)
        self.assertEqual(result["aspect_ratio"], "16:9")
        self.assertNotIn("02:29", str(inspection.aspect_ratio_controls))
        self.assertNotIn("1:1", str(inspection.aspect_ratio_controls))

    def test_actual_mismatched_formats_fail_closed(self):
        for ratio in ("crop_9_16", "crop_1_1", "4:3"):
            with self.subTest(ratio=ratio):
                session, inspection = self.inspect([{"text": ratio}])
                self.assertEqual(inspection.aspect_ratio_controls, (ratio,))
                with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
                    session.configure_generation(GenerationSettings(variants=1), inspection=inspection)

    def test_missing_current_ratio_cannot_use_matching_history(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([], history=[{"text": "crop_16_9"}], title="")

    def test_conflicting_current_ratios_fail_closed(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"text": "crop_16_9"}, {"text": "crop_9_16"}])

    def test_hidden_settings_and_unselected_options_are_not_current(self):
        result = self.configure([
            {"text": "crop_16_9"},
            {"text": "9:16", "attrs": {"hidden": "true"}},
            {"text": "1:1", "attrs": {"role": "radio", "aria-checked": "false"}},
        ])
        self.assertEqual(result["aspect_ratio"], "crop_16_9")

    def test_only_unselected_option_is_insufficient(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"text": "16:9", "attrs": {"role": "radio", "aria-checked": "false"}}], title="")

    def test_selected_radio_is_positive_evidence(self):
        result = self.configure([
            {"text": "16:9", "attrs": {"role": "radio", "aria-checked": "true"}},
        ])
        self.assertEqual(result["aspect_ratio"], "16:9")

    def test_accessible_label_cannot_hide_a_conflicting_visible_icon(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"attrs": {"aria-label": "Aspect ratio 16:9"},
                             "children": [{"tag": "span", "text": "crop_9_16"}]}])

    def test_nested_hidden_icon_does_not_supply_selected_ratio(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"children": [{"tag": "span", "text": "crop_16_9",
                                           "attrs": {"hidden": "true"}}]}])

    def test_duplicate_accessible_and_icon_evidence_is_one_ratio(self):
        result = self.configure([{"attrs": {"aria-label": "Aspect ratio 16:9"},
                                 "children": [{"tag": "span", "text": "crop_16_9"}]}],
                                disabledStart=True)
        self.assertIn(result["aspect_ratio"], ("16:9", "crop_16_9"))

    def test_referenced_accessible_label_supplies_current_ratio(self):
        result = self.configure([{"attrs": {"aria-labelledby": "ratio-label"}}],
                                history=[{"tag": "span", "attrs": {"id": "ratio-label"},
                                          "text": "Aspect ratio 16:9"}])
        self.assertEqual(result["aspect_ratio"], "16:9")

    def test_unowned_text_disconnected_controls_and_tiles_are_insufficient(self):
        for setting in (
            {"tag": "span", "text": "crop_16_9"},
            {"text": "crop_16_9", "attrs": {"connected": False}},
            {"tag": "flow-image-tile", "children": [{"text": "crop_16_9"}]},
            {"tag": "div", "attrs": {"role": "dialog"}, "children": [{"text": "crop_16_9"}]},
        ):
            with self.subTest(setting=setting), self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
                self.configure([setting], title="")

    def test_page_level_ancestor_is_not_a_settings_scope(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"text": "crop_16_9"}], composerTag="main")

    def test_different_configured_ratio_uses_actual_evidence(self):
        session, inspection = self.inspect([{"text": "crop_9_16"}])
        result = session.configure_generation(GenerationSettings(variants=1, aspect_ratio="9:16"),
                                              inspection=inspection)
        self.assertEqual(result["aspect_ratio"], "crop_9_16")

    def test_prompt_is_not_returned_as_ratio_evidence(self):
        session, inspection = self.inspect([{"text": "crop_16_9"}],
                                           prompt="PRIVATE_PROMPT_SENTINEL 1:1")
        result = session.configure_generation(GenerationSettings(variants=1), inspection=inspection)
        self.assertNotIn("PRIVATE_PROMPT_SENTINEL", json.dumps(result))
        self.assertNotIn("PRIVATE_PROMPT_SENTINEL", json.dumps(inspection.generation_control_resolution))

    def test_regular_composer_read_does_not_collect_aspects(self):
        _, inspection = self.inspect([{"text": "crop_16_9"}], includeAspectRatio=False)
        self.assertEqual(inspection.aspect_ratio_controls, ())

    def test_instructional_ratio_text_is_not_a_current_setting(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"text": "Use a 16:9 composition"}])

    def test_unselected_menu_button_is_not_a_current_setting(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"tag": "div", "attrs": {"role": "menu"},
                             "children": [{"text": "16:9"}]}])

    def test_aggregate_combobox_text_cannot_adopt_unselected_descendant(self):
        with self.assertRaisesRegex(FlowAutomationError, "aspect ratio"):
            self.configure([{"attrs": {"role": "combobox"}, "children": [
                {"tag": "div", "attrs": {"role": "listbox"}, "children": [
                    {"text": "16:9", "attrs": {"role": "option", "aria-selected": "false"}},
                ]},
            ]}])

    def test_explicit_selected_option_survives_unselected_siblings(self):
        result = self.configure([{"attrs": {"role": "combobox"}, "children": [
            {"tag": "div", "attrs": {"role": "listbox"}, "children": [
                {"text": "16:9", "attrs": {"role": "option", "aria-selected": "true"}},
                {"text": "9:16", "attrs": {"role": "option", "aria-selected": "false"}},
            ]},
        ]}])
        self.assertEqual(result["aspect_ratio"], "16:9")


if __name__ == "__main__":
    unittest.main()

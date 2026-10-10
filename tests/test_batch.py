import json
import tempfile
import unittest
from pathlib import Path

from nano_batch.batch import (
    SINGLE_PROMPT_VISUAL_ID,
    load_batch,
    load_prompts_file,
    single_prompt_batch,
)


class BatchTests(unittest.TestCase):
    def write_batch(self, data):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "batch.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_prompts(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "prompts.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_youtube_factory_generation_prompt(self):
        path = self.write_batch({"schema": "youtube-factory-external-visual-batch", "items": [{"order": 1, "visual_id": "v1", "generation_prompt": "hello"}]})
        self.assertEqual(load_batch(path).items[0].prompt, "hello")

    def test_loads_simple_prompt(self):
        path = self.write_batch({"items": [{"order": 1, "visual_id": "v1", "prompt": "hello"}]})
        self.assertEqual(load_batch(path).items[0].prompt, "hello")

    def test_single_prompt_batch_uses_one_order_and_one_variant(self):
        batch = single_prompt_batch("A cinematic city at night")

        self.assertEqual(len(batch.items), 1)
        item = batch.items[0]
        self.assertEqual(item.order, 1)
        self.assertEqual(item.visual_id, SINGLE_PROMPT_VISUAL_ID)
        self.assertEqual(item.prompt, "A cinematic city at night")
        self.assertEqual(item.mode, "image")
        self.assertEqual(item.aspect_ratio, "16:9")
        self.assertEqual(item.preferred_model_hint, "Nano Banana 2")
        self.assertEqual(item.variants, 1)

    def test_single_prompt_batch_preserves_unicode_and_internal_whitespace(self):
        batch = single_prompt_batch("  東京の夜景  —  café ☕  ")

        self.assertEqual(batch.items[0].prompt, "東京の夜景  —  café ☕")

    def test_single_prompt_batch_rejects_empty_or_whitespace_only_prompt(self):
        for prompt in ("", "   \t\r\n"):
            with self.subTest(prompt=repr(prompt)), self.assertRaisesRegex(
                ValueError,
                "Prompt must be a non-empty string",
            ):
                single_prompt_batch(prompt)

    def test_single_prompt_batch_identity_is_prompt_sensitive_and_deterministic(self):
        first = single_prompt_batch("same prompt")
        repeated = single_prompt_batch("same prompt")
        different = single_prompt_batch("different prompt")

        self.assertEqual(first.batch_id, repeated.batch_id)
        self.assertNotEqual(first.batch_id, different.batch_id)

    def test_prompts_file_ignores_blank_whitespace_and_comment_lines_in_order(self):
        batch = load_prompts_file(
            self.write_prompts(
                "  first prompt  \n\n   # annotation\n\t\nsecond  prompt\n third prompt \n"
            )
        )

        self.assertEqual([item.order for item in batch.items], [1, 2, 3])
        self.assertEqual(
            [item.visual_id for item in batch.items],
            [
                "prompt_0001_visual_001",
                "prompt_0002_visual_001",
                "prompt_0003_visual_001",
            ],
        )
        self.assertEqual(
            [item.prompt for item in batch.items],
            ["first prompt", "second  prompt", "third prompt"],
        )
        self.assertTrue(all(item.variants == 1 for item in batch.items))

    def test_prompts_file_preserves_unicode(self):
        batch = load_prompts_file(self.write_prompts("東京の夜景\ncafé ☕\nمدينة مستقبلية\n"))

        self.assertEqual(
            [item.prompt for item in batch.items],
            ["東京の夜景", "café ☕", "مدينة مستقبلية"],
        )

    def test_prompts_file_rejects_all_ignored_lines(self):
        path = self.write_prompts("\n  \t\n # first note\n#second note\n")

        with self.assertRaisesRegex(ValueError, "contains no prompts"):
            load_prompts_file(path)

    def test_prompts_file_identity_tracks_accepted_prompt_content(self):
        first = load_prompts_file(self.write_prompts("one\ntwo\n"))
        same = load_prompts_file(self.write_prompts(" one \n# note\ntwo\n"))
        changed = load_prompts_file(self.write_prompts("one\nchanged\n"))

        self.assertEqual(first.batch_id, same.batch_id)
        self.assertNotEqual(first.batch_id, changed.batch_id)

    def test_duplicate_visual_id_rejected(self):
        path = self.write_batch({"items": [{"order": 1, "visual_id": "same", "prompt": "a"}, {"order": 2, "visual_id": "same", "prompt": "b"}]})
        with self.assertRaises(ValueError):
            load_batch(path)

    def test_duplicate_order_rejected(self):
        path = self.write_batch({"items": [{"order": 1, "visual_id": "a", "prompt": "a"}, {"order": 1, "visual_id": "b", "prompt": "b"}]})
        with self.assertRaises(ValueError):
            load_batch(path)

    def test_non_contiguous_order_rejected(self):
        path = self.write_batch({"items": [{"order": 1, "visual_id": "a", "prompt": "a"}, {"order": 3, "visual_id": "b", "prompt": "b"}]})
        with self.assertRaises(ValueError):
            load_batch(path)

    def test_validated_canonical_order_is_deterministic_for_generic_item_count(self):
        path = self.write_batch(
            {
                "items": [
                    {
                        "order": order,
                        "visual_id": f"visual-{order}",
                        "prompt": f"prompt-{order}",
                    }
                    for order in range(10, 0, -1)
                ]
            }
        )
        batch = load_batch(path)
        self.assertEqual([item.order for item in batch.items], list(range(1, 11)))
        self.assertEqual(
            [(item.visual_id, item.prompt) for item in batch.items],
            [(f"visual-{order}", f"prompt-{order}") for order in range(1, 11)],
        )

    def test_batch_id_deterministic(self):
        data = {"items": [{"order": 1, "visual_id": "a", "prompt": "a"}, {"order": 2, "visual_id": "b", "prompt": "b"}]}
        self.assertEqual(load_batch(self.write_batch(data)).batch_id, load_batch(self.write_batch(data)).batch_id)

    def test_explicit_generation_metadata_changes_semantic_batch_identity(self):
        base = {
            "items": [
                {
                    "order": 1,
                    "visual_id": "v1",
                    "prompt": "safe prompt",
                    "mode": "image",
                    "aspect_ratio": "16:9",
                    "preferred_model_hint": "Nano Banana Pro",
                    "variants": 2,
                }
            ]
        }
        nb2 = json.loads(json.dumps(base))
        nb2["items"][0]["preferred_model_hint"] = "Nano Banana 2"

        self.assertNotEqual(
            load_batch(self.write_batch(base)).batch_id,
            load_batch(self.write_batch(nb2)).batch_id,
        )

    def test_sample_batch_51_declares_nb2_16_by_9_and_two_variants(self):
        path = Path(__file__).resolve().parents[1] / "data" / "sample_batch_51.json"
        batch = load_batch(path)

        self.assertEqual(len(batch.items), 51)
        self.assertEqual([item.order for item in batch.items], list(range(1, 52)))
        self.assertTrue(all(item.mode == "image" for item in batch.items))
        self.assertTrue(all(item.aspect_ratio == "16:9" for item in batch.items))
        self.assertTrue(
            all(item.preferred_model_hint == "Nano Banana 2" for item in batch.items)
        )
        self.assertTrue(all(item.variants == 2 for item in batch.items))
        self.assertNotEqual(
            batch.batch_id,
            "sha256:e085c207489f4cedaafb6099268405dc8d219bd18db779b6dd26da481b9e3a2f",
        )


if __name__ == "__main__":
    unittest.main()

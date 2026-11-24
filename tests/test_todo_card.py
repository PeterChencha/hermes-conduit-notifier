"""The todo card: what the phone draws, and when it is drawn again.

The interesting cases are not the happy path. A checklist is binary and a todo list is not, so the
markers carry state. The schema caps a card at 40 items, so the card must be a picture of what is
left rather than an archive - and it must say on its face that it left something out. And an
identical list must not be drawn twice, because the hook fires on every todo edit.

Written with unittest so it runs on the same interpreter the plugin does, with no test dependency
beyond the standard library (pytest collects these too).
"""

import json
import unittest
from unittest import mock

from conduit_push import card_schema, todo_card


def row(id, content, status="pending", parent=None):
    item = {"id": id, "content": content, "status": status}
    if parent:
        item["parent"] = parent
    return item


class TestParse(unittest.TestCase):
    def test_accepts_a_dict_a_json_string_and_nothing_else(self):
        rows = [row("a", "one")]
        self.assertEqual(todo_card.parse({"todos": rows}), rows)
        self.assertEqual(todo_card.parse(json.dumps({"todos": rows})), rows)
        self.assertEqual(todo_card.parse("not json"), [])
        self.assertEqual(todo_card.parse(None), [])
        self.assertEqual(todo_card.parse({"todos": "nope"}), [])


class TestRender(unittest.TestCase):
    def test_status_is_carried_by_the_marker_because_a_checkbox_is_binary(self):
        card = todo_card.render_card([
            row("a", "done thing", "completed"),
            row("b", "running thing", "in_progress"),
            row("c", "waiting thing", "pending"),
            row("d", "dropped thing", "cancelled"),
        ])
        texts = [i["text"] for i in card["checklist"]]
        self.assertTrue(texts[0].startswith("\u2713 "))     # completed
        self.assertTrue(texts[1].startswith("\u25b8 "))     # in progress
        self.assertFalse(texts[2].startswith(("\u2713", "\u25b8", "\u2715")))
        self.assertTrue(texts[3].startswith("\u2715 "))     # cancelled

    def test_a_theme_is_a_heading_with_a_scoreline_and_never_an_item(self):
        card = todo_card.render_card([
            row("t", "THE END GOAL — every mechanism found on either machine"),
            row("m", "MECHANISMS", "completed", parent="t"),
            row("m2", "GATES", parent="t"),
            row("leaf", "soak the hosts", parent="m2"),
            row("solo", "a root task with no work under it"),
        ])
        texts = " ".join(i["text"] for i in card["checklist"])
        self.assertNotIn("THE END GOAL", texts, "a theme is a heading, not a tickable row")
        self.assertEqual(card["checklist"][0]["group"], "THE END GOAL · 1/2")
        self.assertEqual(card["checklist"][1]["group"], "THE END GOAL · 1/2")
        solo = next(i for i in card["checklist"] if "root task" in i["text"])
        self.assertIsNone(solo.get("group"), "a root row with no children stands alone")

    def test_depth_is_shown_because_a_checklist_cannot_indent(self):
        card = todo_card.render_card([
            row("t", "THEME"),
            row("m", "major row", "in_progress", parent="t"),
            row("s", "a seat under it", parent="m"),
        ])
        texts = [i["text"] for i in card["checklist"]]
        self.assertTrue(any(t.endswith("major row") and not t.startswith("\u00b7") for t in texts),
                        "depth 1 reads plain (only the status marker leads it)")
        self.assertTrue(any(t.startswith("· a seat") for t in texts), "depth 2 carries a leading dot")
        self.assertTrue(all(i["group"] == "THEME · 0/1" for i in card["checklist"]))

    def test_the_card_is_a_picture_of_what_is_left(self):
        rows = [row("a%d" % i, "active %d" % i, "in_progress") for i in range(39)]
        rows += [row("d%d" % i, "done %d" % i, "completed") for i in range(5)]
        card = todo_card.render_card(rows)
        self.assertEqual(len(card["checklist"]), todo_card.MAX_ITEMS)
        texts = [i["text"] for i in card["checklist"]]
        self.assertEqual(sum(1 for t in texts if t.startswith("\u2713")), 1)   # one done row fitted in
        self.assertIn("4 not shown", card["title"])

    def test_the_plan_s_own_order_is_never_rearranged(self):
        card = todo_card.render_card([row("b", "second", "in_progress"), row("a", "first", "completed")])
        self.assertEqual([i["text"] for i in card["checklist"]][0], "\u25b8 second")

    def test_active_work_is_never_crowded_out_by_finished_rows(self):
        rows = [row("d%d" % i, "done %d" % i, "completed") for i in range(45)]
        rows += [row("a%d" % i, "active %d" % i, "pending") for i in range(3)]
        card = todo_card.render_card(rows)
        texts = [i["text"] for i in card["checklist"]]
        self.assertEqual([t for t in texts if "active" in t], ["active 0", "active 1", "active 2"],
                         "the cap must eat finished rows, never active work")
        self.assertIn("8 not shown", card["title"])

    def test_a_long_item_is_one_line_and_fits_the_schema(self):
        card = todo_card.render_card([row("a", "x " * 200)])
        text = card["checklist"][0]["text"]
        self.assertLessEqual(len(text), todo_card.MAX_TEXT)
        self.assertNotIn("\n", text)
        self.assertTrue(text.endswith("\u2026"))

    def test_the_card_passes_the_real_schema(self):
        card = todo_card.render_card([
            row("t", "THE END GOAL"),
            row("m", "MECHANISMS" * 20, parent="t"),
            row("leaf", "soak the hosts " * 30, parent="m"),
            row("d", "finished", "completed"),
        ])
        notes = []
        self.assertNotEqual(card_schema.normalize_cards([card], notes), [])
        self.assertEqual(notes, [])

    def test_an_empty_list_draws_nothing(self):
        self.assertIsNone(todo_card.render_card([]))


class TestPush(unittest.TestCase):
    def test_the_same_list_is_not_drawn_twice(self):
        sent = []

        def fake_handle(args, session_id=None):
            sent.append((args, session_id))
            return json.dumps({"ok": True})

        with mock.patch.object(todo_card.show_tool, "available", lambda: True), \
                mock.patch.object(todo_card.show_tool, "handle", fake_handle):
            payload = {"todos": [row("a", "one", "in_progress")]}
            self.assertTrue(todo_card.push(payload, "s1"))
            self.assertFalse(todo_card.push(payload, "s1"))          # identical: no second card
            self.assertEqual(len(sent), 1)
            todo_card.forget("s1")
            self.assertTrue(todo_card.push(payload, "s1"))           # a new chat starts clean
            self.assertEqual(len(sent), 2)

    def test_the_board_carries_a_title_and_markdown_of_its_own(self):
        # The store refuses a board without them ("no_title"), however valid the checklist is.
        sent = []

        def fake_handle(args, session_id=None):
            sent.append(args)
            return json.dumps({"ok": True})

        with mock.patch.object(todo_card.show_tool, "available", lambda: True), \
                mock.patch.object(todo_card.show_tool, "handle", fake_handle):
            self.assertTrue(todo_card.push({"todos": [row("a", "one", "in_progress")]}, "s5"))
        args = sent[0]
        self.assertEqual(args["title"], args["cards"][0]["title"])
        self.assertLessEqual(len(args["title"]), 80)
        self.assertIn("one", args["markdown"], "the fallback is the card's own text")
        self.assertLessEqual(len(args["markdown"]), todo_card.MAX_MARKDOWN + 40)

    def test_a_phone_that_is_away_gets_no_card_and_no_error(self):
        with mock.patch.object(todo_card.show_tool, "available", lambda: False):
            self.assertFalse(todo_card.push({"todos": [row("a", "one")]}, "s2"))

    def test_a_refused_card_is_not_remembered(self):
        with mock.patch.object(todo_card.show_tool, "available", lambda: True), \
                mock.patch.object(todo_card.show_tool, "handle",
                                  lambda *a, **k: json.dumps({"ok": False, "error": "nope"})):
            self.assertFalse(todo_card.push({"todos": [row("a", "one")]}, "s3"))
            self.assertNotIn("s3", todo_card._shown)

    def test_a_broken_card_never_raises_into_the_turn(self):
        with mock.patch.object(todo_card.show_tool, "available", lambda: True), \
                mock.patch.object(todo_card.show_tool, "handle", side_effect=RuntimeError("boom")):
            self.assertFalse(todo_card.push({"todos": [row("a", "one")]}, "s4"))


class TestSave(unittest.TestCase):
    def test_the_list_is_written_atomically_and_read_back(self):
        import tempfile
        from pathlib import Path

        home = Path(tempfile.mkdtemp())
        rows = [row("a", "one", "in_progress")]
        with mock.patch.object(todo_card, "get_hermes_home", lambda: str(home)):
            self.assertTrue(todo_card.save({"todos": rows}))
        data = json.loads((home / todo_card.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(data, {"version": 1, "rows": rows})
        self.assertFalse((home / "todo.json.tmp").exists(), "the temp file is replaced, not left behind")

    def test_nothing_usable_to_save_is_not_an_error(self):
        self.assertFalse(todo_card.save({"todos": []}))
        self.assertFalse(todo_card.save("not json"))

    def test_an_unwritable_home_never_raises_into_the_turn(self):
        with mock.patch.object(todo_card, "get_hermes_home", side_effect=RuntimeError("boom")):
            self.assertFalse(todo_card.save({"todos": [row("a", "one")]}))


if __name__ == "__main__":
    unittest.main()

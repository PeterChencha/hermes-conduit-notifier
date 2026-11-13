"""The session's todo list, drawn on the phone: one checklist card that keeps itself current.

The desktop's ``todo_list`` is per-session tool state. It reaches desktop clients as ``todo_state``
(gateway/session_controls, local_viewer_events) and reaches Conduit by nothing at all: this plugin
never mentioned ``todo_list`` before this module. So the phone could see that a "planning" step was
running (live_activity maps ``todo`` -> planning) and never what the plan was.

This closes that gap the only way Conduit supports it: a checklist card with a **live name**, shown
again whenever the list changes, so the phone replaces the card in place instead of stacking one per
edit. Ticks are kept host-side (checklist_store) and come back at the next turn, which is why the
items carry their state as a marker - a checklist is binary, the todo list is not.

Nothing here raises into a turn: a phone that is away, a card Conduit refuses, a payload that is not
JSON - each one is a quiet no-op, because a todo edit must never fail because a card could not be
drawn.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from . import card_schema, show_tool

logger = logging.getLogger(__name__)

LIVE_NAME = "hermes-todo"
MAX_ITEMS = 40
MAX_MARKDOWN = 6000          # a board's Markdown body, under the store's own limit                 # the checklist schema's own cap
MAX_TEXT = 120                 # its per-item cap
MAX_GROUP = 40                 # its per-group cap
MAX_TITLE = 80                 # MAX_CARD_TITLE_CHARS

# A checklist is binary; the todo list is not. The marker carries the state the checkbox cannot.
_MARKERS = {"completed": "\u2713 ", "in_progress": "\u25b8 ", "pending": "", "cancelled": "\u2715 "}
_ACTIVE = {"pending", "in_progress"}

# Last card pushed per session, so an identical list is not drawn twice.
_shown: dict[str, str] = {}


def _clean(text: Any, limit: int) -> str:
    """One line, no padding, cut on the limit with an ellipsis that fits inside it."""
    line = " ".join(str(text or "").split())
    if len(line) <= limit:
        return line
    return line[: max(1, limit - 1)].rstrip() + "\u2026"


def _label(text: Any) -> str:
    """The short name of a theme: what stands before the dash that introduces its sentence.

    "END GOAL - a new session on either machine finds every mechanism" is a heading and a
    paragraph; a group heading on a phone can only be the heading.
    """
    line = _clean(text, MAX_GROUP)
    for dash in (" \u2014 ", " - ", ": ", ":"):
        if dash in line:
            head = line.split(dash)[0].strip()
            if 1 < len(head) <= MAX_GROUP:
                return head
    return line


def parse(result: Any) -> list[dict[str, Any]]:
    """The todo rows out of a tool result: a dict, a JSON string, or nothing usable."""
    value = result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, dict):
        return []
    rows = value.get("todos")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and _clean(r.get("content"), 1)]


def render_card(rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any] | None:
    """A checklist card for the list, or None when there is nothing to draw.

    Active work leads: subtasks keep their parent as their group, and when the list is longer than
    the schema allows, finished rows are the ones left out - the card is a picture of what is left,
    not an archive. The title says how many were left out so the phone never looks complete when it
    is not.
    """
    if not rows:
        return None

    by_id = {str(r.get("id")): r for r in rows if r.get("id") is not None}
    kids: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("parent"):
            kids.setdefault(str(r["parent"]), []).append(r)
    # A theme is a top-level row with work under it. It is a heading on the phone, not a tickable
    # line, and its heading carries the scoreline the panel gives it: green rows over rows.
    themes = {str(r.get("id")): r for r in rows if not r.get("parent") and kids.get(str(r.get("id")))}

    def theme_of(row: dict[str, Any]) -> tuple[str | None, int]:
        parent, depth, seen = row.get("parent"), 0, set()
        while parent and str(parent) in by_id and str(parent) not in seen:
            seen.add(str(parent))
            depth += 1
            up = by_id[str(parent)]
            if str(up.get("id")) in themes:
                return str(up.get("id")), depth
            parent = up.get("parent")
        return None, depth

    def heading(theme: dict[str, Any]) -> str:
        sub = kids.get(str(theme.get("id")), [])
        green = sum(1 for k in sub if str(k.get("status")) == "completed")
        score = " \u00b7 %d/%d" % (green, len(sub))
        name = _label(theme.get("content")) or "Tasks"
        return _clean(name[: max(1, MAX_GROUP - len(score))].rstrip() + score, MAX_GROUP)

    open_n = sum(1 for r in rows if str(r.get("status")) in _ACTIVE and str(r.get("id")) not in themes)
    done_n = sum(1 for r in rows if str(r.get("status")) == "completed")

    # Order is the plan's own shape - theme, then major row, then subtask - so it is never
    # rearranged. Over the cap, finished rows go first (from the back), because a picture of what is
    # left is the point; only if that is not enough does the tail get cut.
    keep, dropped = [r for r in rows if str(r.get("id")) not in themes], 0
    i = len(keep) - 1
    while len(keep) > MAX_ITEMS and i >= 0:
        if str(keep[i].get("status")) not in _ACTIVE:
            keep.pop(i)
            dropped += 1
        i -= 1
    while len(keep) > MAX_ITEMS:
        keep.pop()
        dropped += 1

    items: list[dict[str, str]] = []
    for row in keep:
        marker = _MARKERS.get(str(row.get("status")), "")
        theme_id, depth = theme_of(row)
        # depth is shown, because a checklist cannot indent: a major row reads plain, a seat under
        # it reads with a leading dot, so the theme -> major -> subtask shape survives the card.
        lead = "\u00b7 " * max(0, depth - 1)
        item = {"text": _clean(marker + lead + str(row.get("content") or ""), MAX_TEXT)}
        if theme_id and theme_id in themes:
            item["group"] = heading(themes[theme_id])
        items.append(item)

    title = "Todo \u2014 %d open" % open_n
    if done_n:
        title += ", %d done" % done_n
    if dropped:
        title += " (%d not shown)" % dropped
    return {"type": "checklist", "title": _clean(title, MAX_TITLE), "live": LIVE_NAME, "checklist": items}


def push(result: Any, session_id: Any) -> bool:
    """Draw the list on the phone if it changed. True when a card was sent.

    Called after every ``todo_list`` call. Skipped when Conduit is not linked, when the list renders
    empty, when the same session already has this exact card, and when Conduit refuses it.
    """
    rows = parse(result)
    card = render_card(rows)
    if card is None:
        return False
    sid = str(session_id or "")
    digest = hashlib.sha256(json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    if _shown.get(sid) == digest:
        return False
    try:
        if not show_tool.available():
            return False
        notes: list[str] = []
        if not card_schema.normalize_cards([card], notes):
            logger.debug("conduit_push: todo card refused: %s", "; ".join(notes) or "no reason given")
            return False
        # A board needs its own title and a Markdown body: without them the store refuses the card
        # ("no_title") even when the checklist itself is valid. The Markdown is what a Conduit that
        # can't draw the card shows instead, so it is the card's own fallback, cut to a safe size.
        markdown = card_schema.fallback_markdown([card])
        if len(markdown) > MAX_MARKDOWN:
            markdown = markdown[:MAX_MARKDOWN].rsplit("\n", 1)[0] + "\n\n_(cut to fit)_"
        answer = json.loads(show_tool.handle({"cards": [card], "title": card["title"], "markdown": markdown},
                                             session_id=sid))
        if not isinstance(answer, dict) or not answer.get("ok", True):
            return False
    except Exception:  # noqa: BLE001 - a card must never break a turn
        logger.debug("conduit_push: todo card not shown", exc_info=True)
        return False
    _shown[sid] = digest
    if len(_shown) > 64:                       # a long-lived process, a bounded map
        _shown.pop(next(iter(_shown)))
    return True


def forget(session_id: Any) -> None:
    """Drop the fingerprint for a session (a new chat starts with no card)."""
    _shown.pop(str(session_id or ""), None)

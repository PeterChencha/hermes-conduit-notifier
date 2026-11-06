"""Render a Markdown file as a standalone HTML page for Conduit's page viewer.

The dashboard loads this module by path from ``artifacts_index.py``, so it
imports nothing from the plugin package. Markdown is parsed with markdown-it-py
(GFM tables, task lists, strikethrough and footnotes); raw HTML in the source
is escaped, and links keep markdown-it's safe-scheme filter. A host without
markdown-it-py, or a file that is too large or not UTF-8, gets ``None`` and the
caller keeps the original document artifact.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import stat as stat_module
import time
from pathlib import Path
from typing import Optional

try:  # The host's Hermes environment ships markdown-it-py; a bare Python may not.
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - exercised only on a host without it
    MarkdownIt = None  # type: ignore[assignment]

# Bump when the page wrapper or CSS changes, so cached pages are rewritten.
RENDERER_VERSION = "1"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
# Conduit refuses an HTML preview over 4 MB before it decodes it; stay under it.
MAX_PAGE_BYTES = 3_500_000
CACHE_KEEP = 500
CACHE_MAX_AGE_S = 30 * 24 * 60 * 60
_MARKDOWN_EXTENSIONS = {"md", "markdown", "mdown", "mkd"}
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_TABLE = re.compile(r"<table>(.*?)</table>", re.DOTALL)

_md = None


def is_markdown(path: str) -> bool:
    """True when the path names a Markdown file (query and fragment ignored)."""
    return _extension(path) in _MARKDOWN_EXTENSIONS


def _local(path: str) -> str:
    """The file path without a URL query or fragment; Windows names cannot hold them."""
    return str(path).split("?", 1)[0].split("#", 1)[0]


def _extension(path: str) -> str:
    name = re.split(r"[/\\]", _local(path))[-1]
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def _parser():
    global _md
    if _md is None:
        if MarkdownIt is None:
            return None
        # html=False escapes raw HTML; linkify stays off so a bare path is not
        # turned into a link. gfm-like2 adds tables, task lists and footnotes.
        _md = MarkdownIt("gfm-like2", {"html": False, "linkify": False, "typographer": False})
    return _md


def render_page(text: str, *, title: str = "Markdown") -> Optional[str]:
    """The complete HTML document for Markdown text, or None without a parser."""
    parser = _parser()
    if parser is None:
        return None
    try:
        body = parser.render(text)
    except Exception:
        return None
    body = _TABLE.sub(r'<div class="table-wrap"><table>\1</table></div>', body)
    front = ""
    match = _FRONTMATTER.match(text)
    if match:
        yaml_text = match.group(1).strip()
        if yaml_text:
            front = ('<details class="frontmatter"><summary>Frontmatter</summary>'
                     f"<pre>{html.escape(yaml_text)}</pre></details>")
    return _DOCUMENT.format(title=html.escape(title or "Markdown"), front=front, body=body)


def page_for(path: str, cache_root: str, *, max_source_bytes: int = MAX_SOURCE_BYTES,
             max_page_bytes: int = MAX_PAGE_BYTES) -> Optional[str]:
    """The cached HTML page for a Markdown file, rendering it on first use.

    Returns None for a missing file, a non-Markdown extension, a non-regular
    file, a file over the source ceiling, a binary/non-UTF-8 file, a page over
    the viewer's ceiling, or a host without markdown-it-py. The caller then
    keeps the original document artifact and Conduit's Quick Look fallback.
    """
    if not is_markdown(path):
        return None
    local = _local(path)
    try:
        info = os.stat(os.path.expanduser(local))
    except (OSError, ValueError):
        return None
    if not stat_module.S_ISREG(info.st_mode) or info.st_size > max_source_bytes:
        return None
    try:
        root = Path(cache_root).expanduser()
    except (TypeError, ValueError):
        return None
    key = hashlib.sha256(
        f"{RENDERER_VERSION}\0{os.path.abspath(os.path.expanduser(local))}\0"
        f"{info.st_mtime_ns}\0{info.st_size}".encode("utf-8", "surrogatepass")
    ).hexdigest()[:32]
    target = root / f"{key}.html"
    try:
        if target.is_file() and target.stat().st_size > 0:
            return str(target)
    except OSError:
        pass
    try:
        text = Path(os.path.expanduser(local)).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    page = render_page(text, title=os.path.basename(local))
    if page is None:
        return None
    data = page.encode("utf-8")
    if len(data) > max_page_bytes:
        return None
    try:
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".{key}.{os.getpid()}.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError:
        return None
    _prune(root)
    return str(target)


def _prune(root: Path) -> None:
    """Keep the page cache bounded: 500 newest, nothing older than 30 days."""
    now = time.time()
    try:
        entries = [entry for entry in os.scandir(root) if entry.is_file()]
    except OSError:
        return
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    for position, entry in enumerate(entries):
        try:
            if position >= CACHE_KEEP or now - entry.stat().st_mtime > CACHE_MAX_AGE_S:
                os.unlink(entry.path)
        except OSError:
            continue


_DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    color: var(--foreground, #1c1c1e);
    background: var(--background, #ffffff);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 17px;
    line-height: 1.55;
    -webkit-text-size-adjust: 100%;
    overflow-wrap: anywhere;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: var(--foreground, #f2f2f7); background: var(--background, #000000); }}
  }}
  .markdown-body {{ max-width: 900px; margin: 0 auto; padding: 18px 16px 48px; }}
  .markdown-body > :first-child {{ margin-top: 0; }}
  h1, h2, h3, h4, h5, h6 {{ line-height: 1.25; margin: 1.25em 0 0.5em; font-weight: 700; }}
  h1 {{ font-size: 1.75em; }}
  h2 {{ font-size: 1.45em; }}
  h3 {{ font-size: 1.2em; }}
  h4 {{ font-size: 1.05em; }}
  p, ul, ol, blockquote, table, pre {{ margin: 0 0 1em; }}
  ul, ol {{ padding-left: 1.4em; }}
  li {{ margin: 0.2em 0; }}
  a {{ color: var(--accent, #0a84ff); text-decoration: none; }}
  a:active {{ text-decoration: underline; }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.88em;
    background: rgba(127, 127, 127, 0.15);
    padding: 0.1em 0.32em;
    border-radius: 4px;
  }}
  pre {{
    background: rgba(127, 127, 127, 0.12);
    border-radius: 8px;
    padding: 12px;
    overflow-x: auto;
    white-space: pre;
  }}
  pre code {{ background: none; padding: 0; font-size: 0.88em; }}
  blockquote {{
    margin-left: 0;
    padding: 0.1em 0 0.1em 0.9em;
    border-left: 3px solid var(--border, rgba(127, 127, 127, 0.4));
    color: var(--muted-foreground, #6c6c70);
  }}
  .table-wrap {{ overflow-x: auto; margin: 0 0 1em; }}
  table {{ border-collapse: collapse; min-width: 100%; }}
  th, td {{
    border: 1px solid var(--border, rgba(127, 127, 127, 0.4));
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: rgba(127, 127, 127, 0.08); font-weight: 600; }}
  img {{ max-width: 100%; height: auto; }}
  hr {{ border: 0; border-top: 1px solid var(--border, rgba(127, 127, 127, 0.4)); margin: 1.5em 0; }}
  .task-list-item {{ list-style: none; }}
  .task-list-item-checkbox {{ margin-right: 0.4em; }}
  .markdown-alert {{
    padding: 8px 12px;
    border-left: 4px solid var(--accent, #0a84ff);
    background: rgba(127, 127, 127, 0.08);
    border-radius: 0 6px 6px 0;
    margin: 0 0 1em;
  }}
  .markdown-alert-title {{ font-weight: 700; margin-bottom: 0.2em; }}
  .frontmatter {{
    margin: 0 0 1.2em;
    padding: 8px 12px;
    border: 1px solid var(--border, rgba(127, 127, 127, 0.4));
    border-radius: 8px;
    color: var(--muted-foreground, #6c6c70);
    font-size: 0.9em;
  }}
  .frontmatter summary {{ cursor: pointer; }}
  .frontmatter pre {{ background: none; padding: 6px 0 0; margin: 0; }}
  @media (min-width: 700px) {{
    .markdown-body {{ padding: 28px 28px 64px; }}
    body {{ font-size: 18px; }}
  }}
</style>
</head>
<body>
<article class="markdown-body">
{front}
{body}
</article>
</body>
</html>
"""

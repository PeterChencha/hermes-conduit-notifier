import importlib.util
import os
import pathlib

import pytest

pytest.importorskip("markdown_it")

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("conduit_markdown_page_test", ROOT / "markdown_page.py")
page_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(page_module)


def test_renders_gfm_and_escapes_raw_html(tmp_path):
    source = tmp_path / "plan.md"
    source.write_text(
        "# Plan\n\n"
        "| Step | Owner |\n| --- | --- |\n| Build | Peter |\n\n"
        "- [x] shipped\n- [ ] reviewed\n\n"
        "```python\nprint('ok')\n```\n\n"
        "<script>alert(1)</script>\n",
        encoding="utf-8",
    )
    page = page_module.render_page(source.read_text(encoding="utf-8"), title="plan.md")
    assert page is not None
    assert "<h1>Plan</h1>" in page and "<table>" in page
    assert "task-list-item" in page and 'class="language-python"' in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_frontmatter_is_a_details_card(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("---\ntitle: A note\n---\n# Body\n", encoding="utf-8")
    page = page_module.render_page(source.read_text(encoding="utf-8"))
    assert page is not None and "<details" in page
    assert "title: A note" in page and "<h1>Body</h1>" in page


def test_page_for_caches_and_rewrites_when_the_file_changes(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("# One\n", encoding="utf-8")
    cache = tmp_path / "pages"
    first = page_module.page_for(str(source), str(cache))
    assert first is not None and pathlib.Path(first).is_file()
    assert page_module.page_for(str(source), str(cache)) == first

    source.write_text("# Two\n", encoding="utf-8")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1_000_000))
    second = page_module.page_for(str(source), str(cache))
    assert second is not None and second != first
    assert "<h1>Two</h1>" in pathlib.Path(second).read_text(encoding="utf-8")


def test_page_for_accepts_a_download_query(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("# Query\n", encoding="utf-8")
    page = page_module.page_for(f"{source}?download=1", str(tmp_path / "pages"))
    assert page is not None and "<h1>Query</h1>" in pathlib.Path(page).read_text(encoding="utf-8")


def test_page_for_falls_back_for_non_markdown_missing_and_binary(tmp_path):
    cache = tmp_path / "pages"
    plain = tmp_path / "note.txt"
    plain.write_text("plain", encoding="utf-8")
    assert page_module.page_for(str(plain), str(cache)) is None
    assert page_module.page_for(str(tmp_path / "gone.md"), str(cache)) is None
    binary = tmp_path / "bad.md"
    binary.write_bytes(b"\xff\xfe# bad")
    assert page_module.page_for(str(binary), str(cache)) is None


def test_page_for_refuses_a_source_over_the_ceiling(tmp_path):
    source = tmp_path / "large.md"
    source.write_text("# " + "x" * 100, encoding="utf-8")
    assert page_module.page_for(str(source), str(tmp_path / "pages"), max_source_bytes=10) is None

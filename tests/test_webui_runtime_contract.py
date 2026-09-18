from pathlib import Path


WEBUI = Path(__file__).resolve().parents[1] / "webui.py"


def test_webui_repairs_flattened_markdown_before_rendering():
    source = WEBUI.read_text(encoding="utf-8")
    assert "window.markdownit" in source
    assert "function repairFlattenedMarkdown(text)" in source
    assert "function renderMD(text)" in source
    assert "text = repairFlattenedMarkdown(text);" in source
    assert "text = md.render(text)" in source
    assert "([^\\n])\\s*(#{2,6}\\s+)" in source
    assert "Markdown tables" in source


def test_webui_always_clears_stream_controller():
    source = WEBUI.read_text(encoding="utf-8")
    assert "window._streamController = controller" in source
    assert "if (window._streamController === controller) window._streamController = null;" in source
    assert "btn.disabled = false;" in source


def test_webui_does_not_restore_stale_stream_controller_after_send():
    source = WEBUI.read_text(encoding="utf-8")
    assert "window._streamController = controller;\n}\n\nvar currentRating" not in source


def test_shared_diagnostic_helpers_import_json_for_serialization():
    source = (WEBUI.parents[0] / "tools" / "_shared.py").read_text(encoding="utf-8")
    assert "import json" in source
    assert "return json.dumps(finalize_summary(result), ensure_ascii=False)" in source


def test_all_python_json_attribute_uses_have_a_json_import():
    import ast

    root = WEBUI.parents[0]
    failures = []
    for path in root.rglob("*.py"):
        if any(part.startswith(".") for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue

        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "json":
                        imported_names.add(alias.asname or "json")
            elif isinstance(node, ast.ImportFrom) and node.module == "json":
                imported_names.add("json")

        json_names = {
            node.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in {"dumps", "loads", "dump", "load", "JSONEncoder", "JSONDecoder"}
            and node.value.id == "json"
        }
        if json_names and "json" not in imported_names:
            failures.append(str(path.relative_to(root)))

    assert not failures, "json API used without an explicit json import: " + ", ".join(failures)


def test_webui_repairs_escaped_flattened_tables():
    source = WEBUI.read_text(encoding="utf-8")
    assert "Some model responses escape table pipes" in source
    assert "tableLike" in source
    assert "Flattened tables commonly collapse row boundaries" in source
    assert "Split common section labels" in source
    assert "heading followed by '-' is commonly a flattened bullet list" in source
    assert "String.fromCharCode(96)" in source
    assert "__MD_FENCE_" in source
    assert "Normalize separator rows that use typographic dashes" in source
    assert "row.replace(/[-—–－]+/g, '---')" in source
    assert "Split only when the heading line clearly contains" in source
    assert "s = s.replace(/(^|\\n)(#{2,6} [^\\n|]+)(\\|[^\\n]+\\|)" in source

def test_webui_markdown_repair_declares_fence_regex():
    source = _read_webui()
    assert "var fenceRe = new RegExp(" in source
    assert "s = s.replace(/\\\\\\|/g, '|');" in source

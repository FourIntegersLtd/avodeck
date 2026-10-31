"""Rule 7: identifiers go out before AI, and that includes the subject's name and date of birth.

An AST scan of `app/features/**`: every call to the AI kit's `run` (a `.run(` given `pack=` and `text=`) and
every `pii.strip(` must pass both `names=` and `dates_of_birth=`, and neither may be an empty literal (a
seam left at "nobody"). Get both from `app.core.contracts.case_identity.identity_for`.
"""

from __future__ import annotations

import ast
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "app" / "features"

# (file under app/features, enclosing function) -> why no identity is needed. Only calls with no case.
NO_CASE = {
    ("setup/routes_files.py", "suggest_sections"): "T-02 reads template headings: no case, no patient data",
}

NEEDED = ("names", "dates_of_birth")


def _is_empty_literal(node: ast.expr) -> bool:
    return (isinstance(node, ast.Tuple | ast.List) and not node.elts) or (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"list", "tuple"}
        and not node.args
    )


def _kind(call: ast.Call) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    keys = {k.arg for k in call.keywords}
    if func.attr == "run" and {"pack", "text"} <= keys:
        return "AI kit run"
    if func.attr == "strip" and isinstance(func.value, ast.Name) and func.value.id == "pii":
        return "pii.strip"
    return None


def _functions(tree: ast.AST) -> dict[int, str]:
    """Line of every node -> name of the innermost function holding it."""
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                if hasattr(node, "lineno"):
                    owner[id(node)] = fn.name
    return owner


def _problems() -> tuple[list[str], int]:
    found: list[str] = []
    checked = 0
    for path in sorted(FEATURES.rglob("*.py")):
        rel = path.relative_to(FEATURES).as_posix()
        tree = ast.parse(path.read_text())
        owner = _functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for k in node.keywords:
                if k.arg in NEEDED and _is_empty_literal(k.value):
                    found.append(f"{rel}:{node.lineno} passes an empty {k.arg}")
            if (kind := _kind(node)) is None:
                continue
            if (rel, owner.get(id(node), "")) in NO_CASE:
                continue
            checked += 1
            given = {k.arg: k.value for k in node.keywords if k.arg}
            missing = [n for n in NEEDED if n not in given or _is_empty_literal(given[n])]
            if missing:
                found.append(f"{rel}:{node.lineno} {kind} lacks {', '.join(missing)}")
    return found, checked


def test_every_ai_call_and_strip_passes_the_names_and_dates_of_birth() -> None:
    problems, checked = _problems()
    assert checked > 20, "the scan found the calls"
    assert not problems, "\n".join(problems)


def test_the_exemptions_still_exist() -> None:
    for (rel, function), _ in NO_CASE.items():
        text = (FEATURES / rel).read_text()
        assert f"def {function}(" in text, f"{rel} no longer has {function}: drop the exemption"

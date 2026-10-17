"""Every i18n key the setup API returns has words in the web app (glossary; every interface string is a key).

The keys are found in `app/features/setup/*.py`: each literal `"setup.…"` string, and each f-string key
expanded over the values it can take (EXPANSIONS). A new f-string key with no expansion here fails, so
nobody returns a key the screens cannot show. Plural keys may exist as `<key>_one` and `<key>_other`
(web/src/foundation/i18n/index.ts). Words that come from a pack (conflict questions, question types) are
returned as the pack's words, never as a per-question key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from app.features.setup.notes import NoteTopic

ROOT = Path(__file__).resolve().parents[3]
SETUP = ROOT / "api" / "app" / "features" / "setup"
MESSAGES = ROOT / "web" / "src" / "foundation" / "i18n" / "en-GB.json"

LITERAL = re.compile(r"(?<![f\w])\"(setup\.[a-z0-9_.]+)\"")
FSTRING = re.compile(r"\bf\"(setup\.[^\"]*\{[^\"]*)\"")

EXPANSIONS: dict[str, list[str]] = {
    "setup.conflicts.warning.coverage_{coverage.status}": [
        "setup.conflicts.warning.coverage_none",
        "setup.conflicts.warning.coverage_partial",
    ],
    "setup.notes.{topic}.subject": [f"setup.notes.{t}.subject" for t in get_args(NoteTopic)],
    "setup.notes.{topic}.body": [f"setup.notes.{t}.body" for t in get_args(NoteTopic)],
}


def _messages() -> dict[str, str]:
    messages: dict[str, str] = json.loads(MESSAGES.read_text(encoding="utf-8"))
    return messages


def _has(messages: dict[str, str], key: str) -> bool:
    return key in messages or f"{key}_other" in messages


def returned_keys() -> tuple[set[str], set[str]]:
    keys: set[str] = set()
    unexpanded: set[str] = set()
    for path in sorted(SETUP.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        keys.update(LITERAL.findall(source))
        for pattern in FSTRING.findall(source):
            if pattern in EXPANSIONS:
                keys.update(EXPANSIONS[pattern])
            else:
                unexpanded.add(pattern)
    return keys, unexpanded


def test_every_f_string_key_has_its_values_listed() -> None:
    _, unexpanded = returned_keys()
    assert unexpanded == set(), "list the values these keys can take in EXPANSIONS, or return pack words"


def test_every_key_the_setup_api_returns_has_words() -> None:
    keys, _ = returned_keys()
    messages = _messages()
    for prefix in ("setup.today.", "setup.questions.warning.", "setup.conflicts.warning.", "setup.notes."):
        assert any(k.startswith(prefix) for k in keys), f"no {prefix} keys found: the scan is broken"
    missing = sorted(k for k in keys if not _has(messages, k))
    assert missing == []


def test_a_missing_key_is_caught() -> None:
    messages = {"setup.today.x_one": "a", "setup.today.x_other": "b"}
    assert _has(messages, "setup.today.x")
    assert not _has(messages, "setup.today.y")

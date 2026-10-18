"""The pack loader (architecture 3): jurisdiction, case type, specialty and overlay, merged for a case.

Everything specific to a country, case type or specialty comes from here (rule 11). A pack set is one
folder (`packs/`, or `packs/_fixtures/` in tests) with `jurisdiction/`, `case-type/`, `specialty/` and
`overlay/<jurisdiction>/<specialty>/`. Loading refuses, with the file's path in the message:

- a file with no schema, a file that breaks its schema, a missing required file, a key written twice;
- a jurisdiction or case-type file with a specialty word or code, a specialty file with a country word
  or jurisdiction code (overlays may use any word; lists in `schema/lint_words.yaml`);
- a party role without a label, or a `{placeholder}` that nothing fills, in any combination offered.

Merge rules (3.2): dictionaries merge by key; registries (lists of items with a `key`) are unioned by key,
a later layer may change or disable (`enabled: false`) an entry; other lists and text files are replaced.

`python -m app.core.packs [root ...]` runs the same checks for CI.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jsonschema_rs
import yaml

REPO = Path(__file__).resolve().parents[3]
LAYERS = ("jurisdiction", "case-type", "specialty")

REQUIRED: dict[str, frozenset[str]] = {
    "jurisdiction": frozenset(
        {"pack.yaml", "vocabulary.yaml", "declarations.md", "deadlines.yaml", "holidays.yaml", "report.yaml"}
        | {"identifiers.py"}
    ),
    "case-type": frozenset({"pack.yaml", "workflow.yaml", "questions.yaml", "report_sections.yaml"}),
    "specialty": frozenset({"pack.yaml", "facts.yaml", "sources.yaml", "lanes.yaml"}),
    "overlay": frozenset({"pack.yaml"}),
}
OPTIONAL: dict[str, frozenset[str]] = {
    "jurisdiction": frozenset({"meetings.yaml", "legal_tests.md", "final_checks.yaml", "conflicts.yaml"}),
    "case-type": frozenset({"final_checks.yaml", "declarations.md", "opinion.yaml"}),
    "specialty": frozenset({"checks.yaml", "final_checks.yaml", "vocabulary.yaml"}),
    "overlay": frozenset(
        {"records.yaml", "guidelines.yaml", "vocabulary.yaml", "speech_phrases.yaml", "drug_monitoring.yaml"}
        | {"checks.yaml", "lanes.yaml", "sources.yaml", "final_checks.yaml", "facts.yaml"}
    ),
}
# Lists whose items carry a `key`: unioned by key across layers (3.2).
REGISTRIES = frozenset(
    {"fact_kinds", "source_types", "lanes", "checks", "sections", "appendices", "question_types", "steps"}
    | {"required_contents", "types", "deadlines", "sources"}
)
_SCREEN_ONLY_VOCABULARY = frozenset({"request_notes", "lanes", "as_seen_title"})
CHECK_KIND_LISTS = ("action_kinds", "acknowledged_kinds", "followup_kinds", "or_kinds")
IGNORED = frozenset({"README.md", "__pycache__", ".DS_Store"})
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
END_OF_DAY = time(23, 59, 59)


class PackError(Exception):
    """A pack is missing, invalid or breaks a lint rule. The app does not start with one."""


class DeadlineError(Exception):
    """A deadline cannot be worked out from the pack (unknown kind, set by the court, no holiday data)."""


# --- YAML: no duplicate keys, dates stay strings ------------------------------------------------------------------


class _Loader(yaml.SafeLoader):
    pass


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"key '{key}' is written twice", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _yaml(text: str, rel: str) -> Any:
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise PackError(f"{rel}: {exc}") from exc


def _front_matter(text: str, rel: str) -> dict[str, Any]:
    match = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if match is None:
        raise PackError(f"{rel}: needs front matter between --- lines")
    front = _yaml(match.group(1), rel)
    if not isinstance(front, dict):
        raise PackError(f"{rel}: front matter must be a mapping")
    return {"front": front, "body": match.group(2).strip()}


# --- word lists ---------------------------------------------------------------------------------------------------


def _schema_dir(root: Path) -> Path:
    for candidate in (root / "schema", root.parent / "schema", REPO / "packs" / "schema"):
        if candidate.is_dir():
            return candidate
    raise PackError(f"no schema folder for {root}")


def lint_words(root: Path) -> dict[str, list[str]]:
    """The lint word lists (`schema/lint_words.yaml`) for a pack set."""
    data = yaml.safe_load((_schema_dir(root) / "lint_words.yaml").read_text())
    return {k: [str(w) for w in v] for k, v in data.items() if isinstance(v, list)}


@cache
def _word_pattern(word: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in word.split())
    # All-capital words (acronyms) match only in capitals, so `nice_to_have` is not NICE.
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", 0 if word.isupper() else re.IGNORECASE)


def find_words(text: str, words: list[str]) -> list[str]:
    """The words from `words` that appear in `text` as whole words, in list order."""
    return [w for w in dict.fromkeys(words) if _word_pattern(w).search(text)]


# --- merge rules --------------------------------------------------------------------------------------------------


def _normalise(data: Any, rel: str) -> Any:
    """Registry items become `{key: ...}` dicts; a key written twice in one file is refused."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, items in data.items():
        if name not in REGISTRIES or not isinstance(items, list):
            continue
        seen: set[str] = set()
        normalised = []
        for item in items:
            entry = {"key": item} if isinstance(item, str) else item
            key = entry.get("key") if isinstance(entry, dict) else None
            if isinstance(key, str):
                if key in seen:
                    raise PackError(f"{rel}: '{key}' is listed twice in {name}")
                seen.add(key)
            normalised.append(entry)
        out[name] = normalised
    return out


def _union(earlier: list[Any], later: list[Any]) -> list[Any]:
    by_key: dict[Any, Any] = {}
    loose: list[Any] = []
    for item in [*earlier, *later]:
        if isinstance(item, dict) and "key" in item:
            key = item["key"]
            by_key[key] = {**by_key[key], **item} if key in by_key else dict(item)
        else:
            loose.append(item)
    return [*by_key.values(), *loose]


def _merge(earlier: Any, later: Any) -> Any:
    if not (isinstance(earlier, dict) and isinstance(later, dict)) or {"front", "body"} <= set(later):
        return copy.deepcopy(later)  # scalars, plain lists and text files are replaced whole
    out = copy.deepcopy(earlier)
    for key, value in later.items():
        if key in REGISTRIES and isinstance(out.get(key), list) and isinstance(value, list):
            out[key] = _union(out[key], value)
        elif key in out:
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _enabled(items: list[Any]) -> list[dict[str, Any]]:
    return [copy.deepcopy(i) for i in items if isinstance(i, dict) and i.get("enabled", True) is not False]


# --- one pack -----------------------------------------------------------------------------------------------------


@dataclass
class Pack:
    """One layer's pack after `extends:` is resolved. `files` holds parsed content by file name."""

    layer: str
    code: str
    root: Path
    meta: dict[str, Any]
    files: dict[str, Any]
    sources: dict[str, str]  # file name -> path relative to its pack set, for messages
    raw_text: dict[str, str] = field(default_factory=dict)

    def rel(self, name: str) -> str:
        return self.sources.get(name, f"{self.layer}/{self.code}/{name}")


def _read_pack(root: Path, layer: str, code: str, folder: Path) -> Pack:
    files: dict[str, Any] = {}
    sources: dict[str, str] = {}
    raw: dict[str, str] = {}
    allowed = REQUIRED[layer] | OPTIONAL[layer]
    for path in sorted(folder.iterdir()):
        if path.name in IGNORED or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        if path.name not in allowed:
            raise PackError(f"{rel}: no schema for this file in a {layer} pack")
        text = path.read_text()
        raw[path.name] = text
        sources[path.name] = rel
        if path.suffix == ".yaml":
            files[path.name] = _normalise(_yaml(text, rel), rel)
        elif path.suffix == ".md":
            files[path.name] = _front_matter(text, rel)
        else:
            files[path.name] = path
    meta = files.pop("pack.yaml", None)
    if not isinstance(meta, dict):
        raise PackError(f"{layer}/{code}: missing required file pack.yaml")
    return Pack(layer, code, root, meta, files, sources, raw)


@cache
def _validator(schema_file: Path) -> Any:
    return jsonschema_rs.validator_for(json.loads(schema_file.read_text()))


def _validate(pack: Pack, schema_dir: Path) -> None:
    missing = sorted(REQUIRED[pack.layer] - {"pack.yaml"} - set(pack.files))
    if missing:
        raise PackError(f"{pack.layer}/{pack.code}: missing required file {', '.join(missing)}")
    documents: list[tuple[str, Any]] = [("pack.yaml", pack.meta)]
    for name, content in pack.files.items():
        if name.endswith(".yaml"):
            documents.append((name, content))
        elif name.endswith(".md"):
            documents.append((name, content["front"]))
    for name, content in documents:
        stem = name.rsplit(".", 1)[0]
        schema = schema_dir / f"{pack.layer}.{stem}.schema.json"
        if not schema.exists():
            schema = schema_dir / f"{stem}.schema.json"
        if not schema.exists():
            raise PackError(f"{pack.rel(name)}: no schema ({schema_dir.name}/{pack.layer}.{stem}.schema.json)")
        for error in _validator(schema).iter_errors(content):
            where = "/".join(str(p) for p in error.instance_path) or "the top level"
            raise PackError(f"{pack.rel(name)}: {error.message} (at {where})")
    if pack.layer == "jurisdiction":
        tz = pack.meta["timezone"]
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise PackError(f"{pack.rel('pack.yaml')}: timezone '{tz}' is not a known timezone") from exc
        _identifier_finder(pack.files["identifiers.py"], pack.rel("identifiers.py"))


@cache
def _identifier_module(path: Path, rel: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_avodeck_pack_identifiers_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise PackError(f"{rel}: cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _identifier_finder(path: Path, rel: str) -> Callable[[str], list[tuple[int, int, str]]]:
    module = _identifier_module(path, rel)
    finder = getattr(module, "find_identifiers", None)
    if not callable(finder):
        raise PackError(f"{rel}: must define find_identifiers(text)")
    return finder  # type: ignore[no-any-return]


# --- deadlines ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deadline:
    """`due_on` is the court's date; `due_at` is the end of that day in the court's timezone, in UTC."""

    kind: str
    rule_ref: str
    calendar: str
    due_on: date
    due_at: datetime
    timezone: str

    def shown_in(self, timezone: str) -> datetime:
        """The same moment in the expert's timezone."""
        return self.due_at.astimezone(ZoneInfo(timezone))


# --- the merged pack for one combination --------------------------------------------------------------------------


class MergedPack:
    """Jurisdiction + case type + specialty (+ overlay) for one case, with placeholders filled."""

    def __init__(self, layers: list[Pack]) -> None:
        jurisdiction, case_type, specialty, *rest = layers
        self._j, self._ct, self._sp = jurisdiction.meta, case_type.meta, specialty.meta
        self.codes = (jurisdiction.code, case_type.code, specialty.code)
        label = "+".join(self.codes)
        self.versions = {
            "jurisdiction": f"{jurisdiction.code}@{self._j['version']}",
            "case_type": f"{case_type.code}@{self._ct['version']}",
            "specialty": f"{specialty.code}@{self._sp['version']}",
        }
        if rest:
            self.versions["overlay"] = f"{jurisdiction.code}/{specialty.code}@{rest[0].meta['version']}"
        files: dict[str, Any] = {}
        for pack in layers:
            for name, content in pack.files.items():
                files[name] = _merge(files[name], content) if name in files else copy.deepcopy(content)
        self.vocabulary: dict[str, Any] = files.pop("vocabulary.yaml", {})
        for kind in ("party_roles", "instructing_party_types"):
            labels = self.vocabulary.get(kind, {})
            for key in self._ct[kind]:
                if not isinstance(labels, dict) or not isinstance(labels.get(key), str):
                    raise PackError(f"{label}: {kind} '{key}' has no label in the {jurisdiction.code} vocabulary")
        statuses = self.vocabulary.get("subject_statuses", {})
        for key in self._ct.get("subject_statuses", []):
            if not isinstance(statuses, dict) or not isinstance(statuses.get(key), str):
                raise PackError(f"{label}: subject_statuses '{key}' has no label in the {jurisdiction.code} vocabulary")
        roles = self.vocabulary.get("coverage_roles", {})
        for key in self._sp.get("coverage_roles", []):
            if not isinstance(roles, dict) or not isinstance(roles.get(key), str):
                raise PackError(f"{label}: coverage_roles '{key}' has no label in the vocabulary")
        values = {k: v for k, v in self.vocabulary.items() if isinstance(v, str)}
        values.setdefault("instructing_party", self.instructing_party_types()[0]["label"])
        for key, word in list(self.vocabulary.items()):  # top-level words and lane labels name other words
            if isinstance(word, str) or key == "lanes":
                self.vocabulary[key] = _fill(word, values, f"{label}: vocabulary {key}")
        self._files = {name: _fill(content, values, f"{label}: {name}") for name, content in files.items()}
        self._check_kinds(label)

    # settings
    @property
    def timezone(self) -> str:
        return str(self._j["timezone"])

    @property
    def date_order(self) -> str:
        return str(self._j["date_order"])

    @property
    def locale(self) -> str:
        return str(self._j["locale"])

    @property
    def language(self) -> str:
        return str(self._j["language"])

    @property
    def currency(self) -> str:
        return str(self._j["currency"])

    @property
    def retention_years(self) -> int:
        return int(self._j["default_retention_years"])

    @property
    def quarantine_purge_days(self) -> int:
        """Days a quarantined page is kept before its image and text are deleted (if not told sooner)."""
        return int(self._j["quarantine_purge_days"])

    def record_gap_rules(self) -> dict[str, Any]:
        """Thresholds for where a record source goes quiet (specialty pack `record_gaps`)."""
        return dict(self._sp["record_gaps"])

    @property
    def profession(self) -> str:
        return str(self._sp["profession"])

    @property
    def working_week(self) -> list[int]:
        return list(self._j["working_week"])

    def speech(self) -> dict[str, Any]:
        phrases = self._files.get("speech_phrases.yaml", {}).get("phrases", [])
        return {"locale": self._j["speech"]["locale"], "phrases": list(phrases)}

    # words
    def term(self, key: str) -> str:
        value = self.vocabulary.get(key)
        if not isinstance(value, str):
            raise PackError(f"{'+'.join(self.codes)}: no word for '{key}'")
        return value

    def _paragraph(self, key: str, slots: Mapping[str, str]) -> str:
        notes = self.vocabulary.get("request_notes")
        template = notes.get(key) if isinstance(notes, dict) else None
        if not isinstance(template, str):
            raise PackError(f"{'+'.join(self.codes)}: no request_notes.{key} wording")
        values = {k: v for k, v in self.vocabulary.items() if isinstance(v, str)}
        values.setdefault("instructing_party", self.instructing_party_types()[0]["label"])
        values.update(slots)
        return str(_fill(template, values, key))

    def quarantine_note(self, **slots: str) -> str:
        """The paragraph telling the instructing party which pages were set aside (slot: pages)."""
        return self._paragraph("quarantine", slots)

    def translation_request(self, **slots: str) -> str:
        """The paragraph asking for a certified translation of foreign-language pages (slot: pages)."""
        return self._paragraph("translation", slots)

    def instructing_party_request(self, **slots: str) -> dict[str, str]:
        """The email draft asking the instructing party for missing records (screen 08b): label, subject, body.

        `slots` fill the caller's placeholders (case_reference, missing_items, clearer_copies, expert_name);
        pack words fill the rest. A placeholder with no value raises `PackError`.
        """
        template = self.vocabulary.get("instructing_party_request")
        if not isinstance(template, dict):
            raise PackError(f"{'+'.join(self.codes)}: no instructing_party_request wording")
        values = {k: v for k, v in self.vocabulary.items() if isinstance(v, str)}
        values.setdefault("instructing_party", self.instructing_party_types()[0]["label"])
        values.update(slots)
        filled = _fill({k: template[k] for k in ("label", "subject", "body")}, values, "instructing_party_request")
        return {k: str(v) for k, v in filled.items()}

    def party_roles(self) -> list[dict[str, str]]:
        return [{"key": k, "label": self.vocabulary["party_roles"][k]} for k in self._ct["party_roles"]]

    def instructing_party_types(self) -> list[dict[str, str]]:
        labels = self.vocabulary["instructing_party_types"]
        return [{"key": k, "label": labels[k]} for k in self._ct["instructing_party_types"]]

    def subject_statuses(self) -> list[dict[str, str | None]]:
        """The states the subject can be in (case type), with the jurisdiction's label and, where one
        acts for the subject in that state, the representative's word. Empty when the case type has none."""
        labels = self.vocabulary.get("subject_statuses", {})
        reps = self.vocabulary.get("subject_representatives", {})
        reps = reps if isinstance(reps, dict) else {}
        return [
            {"key": k, "label": labels[k], "representative": reps.get(k)} for k in self._ct.get("subject_statuses", [])
        ]

    def coverage_roles(self) -> list[dict[str, str]]:
        """Kinds of role in the specialty that count when checking the expert practised during the case period."""
        labels = self.vocabulary.get("coverage_roles", {})
        return [{"key": k, "label": labels[k]} for k in self._sp.get("coverage_roles", [])]

    def outside_expertise_question_types(self) -> list[str]:
        """Question types (of this case type) the specialty marks as usually outside its expertise."""
        known = {q["key"] for q in self.question_types()}
        return [k for k in self._sp.get("outside_expertise_question_types", []) if k in known]

    def conflicts(self) -> dict[str, Any]:
        """The jurisdiction's conflict-of-interest questions and the statement wording their answers produce."""
        data = self._files.get("conflicts.yaml")
        if not data:
            return {"questions": [], "statement": {}}
        return copy.deepcopy({"questions": data["questions"], "statement": data["statement"]})

    def expert_roles(self) -> list[str]:
        return list(self._ct["expert_roles"])

    def registration_bodies(self) -> list[str]:
        return list(self._j["registration_bodies"].get(self.profession, []))

    # registries
    def _registry(self, name: str, field_name: str) -> list[dict[str, Any]]:
        return _enabled(self._files.get(name, {}).get(field_name, []))

    def workflow_steps(self) -> list[dict[str, Any]]:
        return self._registry("workflow.yaml", "steps")

    def question_types(self) -> list[dict[str, Any]]:
        return self._registry("questions.yaml", "question_types")

    def report_sections(self) -> list[dict[str, Any]]:
        return self._registry("report_sections.yaml", "sections")

    def fact_kinds(self) -> list[dict[str, Any]]:
        return self._registry("facts.yaml", "fact_kinds")

    def source_types(self) -> list[dict[str, Any]]:
        return self._registry("sources.yaml", "source_types")

    def lanes(self) -> list[dict[str, Any]]:
        """Lanes top to bottom, each with its `kinds` (empty if none) and optional `when` (lanes.schema.json)."""
        return [{**lane, "kinds": lane.get("kinds", [])} for lane in self._registry("lanes.yaml", "lanes")]

    def checks(self) -> list[dict[str, Any]]:
        return self._registry("checks.yaml", "checks")

    def reference_table(self, name: str) -> list[dict[str, Any]]:
        """The rows of the reference table a check names (`table`), from `<name>.yaml` after the layer merge.

        Rows are the file's list field, copied so callers cannot change the pack; no file means no rows.
        KeyError for a name no check gives, so slices never read other pack files this way."""
        if name not in {str(c["table"]) for c in self.checks() if c.get("table")}:
            raise KeyError(f"no check names the reference table {name!r}")
        content = self._files.get(f"{name}.yaml") or {}
        rows = next((v for v in content.values() if isinstance(v, list)), [])
        return copy.deepcopy(rows)

    def own_record_sources(self) -> frozenset[str]:
        """Source types marked `own_record`: the clinician's own record."""
        return frozenset(s["key"] for s in self.source_types() if s.get("own_record") is True)

    def statement_sources(self) -> frozenset[str]:
        """Source types marked `statement`: a witness's own account, not a record."""
        return frozenset(s["key"] for s in self.source_types() if s.get("statement") is True)

    def arriving_kinds(self) -> frozenset[str]:
        """Fact kinds marked `arrives`: never available on their own event date."""
        return frozenset(k["key"] for k in self.fact_kinds() if k.get("arrives") is True)

    def guideline_sources(self) -> list[dict[str, Any]]:
        """The overlay's guideline sources (empty without an overlay)."""
        return self._registry("guidelines.yaml", "sources")

    def guidelines(self) -> list[dict[str, Any]]:
        """Guideline versions from the overlay, each with its jurisdiction, specialty and `reviewed_at`.

        Labels, dates (ISO strings; `in_force_to` None = still in force) and links only (decision 0005)."""
        data = self._files.get("guidelines.yaml", {})
        scope = {"jurisdiction": self.codes[0], "specialty": self.codes[2], "reviewed_at": data.get("reviewed_at")}
        return [{**copy.deepcopy(v), **scope} for v in data.get("versions", [])]

    def _check_kinds(self, label: str) -> None:
        """Every fact kind a lane or check names, and every source a guideline version names, exists."""
        kinds = {k["key"] for k in self.fact_kinds()}
        named: list[tuple[str, str, str]] = []
        for lane in self.lanes():
            named += [("lanes.yaml", f"lane '{lane['key']}'", k) for k in lane.get("kinds", [])]
        for check in self.checks():
            applies = check.get("applies_to", [])
            for field_name in ("applies_to", *CHECK_KIND_LISTS):
                value = applies if field_name == "applies_to" else check.get(field_name, [])
                for kind in [value] if isinstance(value, str) else value:
                    named.append(("checks.yaml", f"check '{check['key']}' {field_name}", kind))
        for name, where, kind in named:
            if kind not in kinds:
                raise PackError(f"{label}: {name}: {where} names '{kind}', which is not a fact kind")
        sources = {s["key"] for s in self.guideline_sources()}
        for version in self.guidelines():
            if version["source"] not in sources:
                raise PackError(
                    f"{label}: guidelines.yaml: {version['code']} names source '{version['source']}', "
                    "which is not listed"
                )

    def final_checks(self) -> list[dict[str, Any]]:
        return self._registry("final_checks.yaml", "checks")

    def deadline_rules(self) -> list[dict[str, Any]]:
        return self._registry("deadlines.yaml", "deadlines")

    def compliance_checklist(self) -> dict[str, Any]:
        report = self._files["report.yaml"]
        return {"rule_ref": report["rule_ref"], "items": _enabled(report["required_contents"])}

    def declarations(self) -> list[dict[str, str]]:
        return [_declaration(self._files["declarations.md"])]

    def legal_tests(self) -> str:
        """Reference text with placeholders filled; empty when the jurisdiction has none."""
        text = self._files.get("legal_tests.md")
        return str(text["body"]) if text else ""

    def identifier_recognisers(self) -> Callable[[str], list[tuple[int, int, str]]]:
        return _identifier_finder(self._files["identifiers.py"], "identifiers.py")

    def normalise_identifier(self, value: str) -> str | None:
        """The jurisdiction's canonical form of a subject identifier, or None if `value` is not one (or the
        jurisdiction defines no normaliser). Used for the duplicate-case check, never stored in plain text."""
        module = _identifier_module(self._files["identifiers.py"], "identifiers.py")
        normalise = getattr(module, "normalise_identifier", None)
        if not callable(normalise):
            return None
        result = normalise(value)
        return result if isinstance(result, str) and result else None

    def _opinion_words(self) -> dict[str, Any]:
        words: dict[str, Any] = self._files.get("opinion.yaml", {})
        return words

    def point_sides(self) -> list[dict[str, str]]:
        """The three sides a fact can support (core codes) with this case type's labels."""
        return [{"code": s["code"], "label": s["label"]} for s in self._opinion_words().get("point_sides", [])]

    def opinion_views(self) -> list[dict[str, str]]:
        """The views an opinion on a question can take (case type), as {code, label}."""
        return [{"code": v["code"], "label": v["label"]} for v in self._opinion_words().get("opinion_views", [])]

    def loaded_language(self) -> list[str]:
        """Phrases the opinion lint flags as loaded."""
        return list(self._opinion_words().get("loaded_language", []))

    def range_note_prompt(self) -> str:
        """The heading over the note on what other practitioners might reasonably do."""
        return str(self._opinion_words().get("range_note_prompt", ""))

    def _who(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "profession": self.profession,
            "clinician": self.term("clinician"),
            "clinicians": self.term("clinicians"),
        }

    def record_hints(self) -> dict[str, Any]:
        return copy.deepcopy(self._files.get("records.yaml", {}))

    def _prompt_vocabulary(self) -> dict[str, Any]:
        """The vocabulary a prompt may see: `request_notes` are email paragraphs and `lanes` / `as_seen_title`
        are screen labels, not prompt words."""
        return {k: copy.deepcopy(v) for k, v in self.vocabulary.items() if k not in _SCREEN_ONLY_VOCABULARY}

    def prompt_context(self, task: str | None = None) -> dict[str, Any]:
        """Pack data injected into neutral prompt templates (no patient data). T-02, T-04 and T-06 get what
        they choose from; every other task gets the default (fact kinds, source types, record hints)."""
        base = {"language": self.language, "vocabulary": self._prompt_vocabulary()}
        if task == "T-06":
            return {
                **base,
                "question_types": [q["key"] for q in self.question_types()],
                "outside_expertise_question_types": self.outside_expertise_question_types(),
            }
        if task == "T-04":
            return {
                **base,
                "date_order": self.date_order,
                "instructing_party_types": self.instructing_party_types(),
                "party_roles": self.party_roles(),
                "court_tracks": list(self._j.get("court_tracks", [])),
            }
        if task in ("T-14a", "T-14b", "T-14c"):
            sides = {"point_sides": self.point_sides()} if task == "T-14a" else {}
            return {**self._who(), **sides}
        if task == "T-15a":
            return {
                **self._who(),
                "specialty": str(self._sp["name"]),
                "outside_expertise_question_types": self.outside_expertise_question_types(),
                "loaded_language": self.loaded_language(),
            }
        if task == "T-02":
            sections = [s["key"] for s in self.report_sections()]
            return {**base, "report_sections": [*sections, *([] if "other" in sections else ["other"])]}
        return {
            "date_order": self.date_order,
            "language": self.language,
            "fact_kinds": [{"key": f["key"], "fields": f.get("fields", {})} for f in self.fact_kinds()],
            "source_types": [{"key": s["key"]} for s in self.source_types()],
            "vocabulary": self._prompt_vocabulary(),
            "record_hints": self.record_hints(),
        }

    # deadlines
    def deadline(self, kind: str, served: date | datetime) -> Deadline:
        """When `kind` falls due after `served`, by the pack's rule, working week and public holidays."""
        rule = next((r for r in self.deadline_rules() if r["key"] == kind), None)
        if rule is None:
            raise DeadlineError(f"no deadline rule '{kind}' in {self.versions['jurisdiction']}")
        if "set_by" in rule:
            raise DeadlineError(f"'{kind}' is set by {rule['set_by']}: enter the date the court gives")
        tz = ZoneInfo(self.timezone)
        if isinstance(served, datetime):
            if served.utcoffset() is None:
                raise ValueError("a service time needs a timezone")
            start = served.astimezone(tz).date()
        else:
            start = served
        calendar = _Calendar(self._files["holidays.yaml"], self.working_week, self.versions["jurisdiction"])
        if rule["calendar"] == "business_days":
            due, counted = start, 0
            while counted < rule["days"]:
                due += timedelta(days=1)
                counted += calendar.is_open(due)
        else:
            due = start + timedelta(days=rule["days"])
            while rule.get("if_closed") == "next_working_day" and not calendar.is_open(due):
                due += timedelta(days=1)
        due_at = datetime.combine(due, END_OF_DAY, tzinfo=tz).astimezone(UTC)
        return Deadline(kind, str(rule["rule_ref"]), str(rule["calendar"]), due, due_at, self.timezone)


class _Calendar:
    def __init__(self, holidays: Mapping[str, Any], working_week: list[int], pack: str) -> None:
        self.start = date.fromisoformat(holidays["covers_from"])
        self.end = date.fromisoformat(holidays["covers_until"])
        self.closed = {date.fromisoformat(d) for d in holidays["dates"]}
        self.week = set(working_week)
        self.pack = pack

    def is_open(self, day: date) -> bool:
        if not self.start <= day <= self.end:
            raise DeadlineError(
                f"{day.isoformat()} is outside the holiday calendar of {self.pack} "
                f"({self.start.isoformat()} to {self.end.isoformat()}); extend holidays.yaml"
            )
        return day.isoweekday() in self.week and day not in self.closed


def _declaration(text: Mapping[str, Any]) -> dict[str, str]:
    front = text["front"]
    fields = {name: str(front[name]) for name in ("key", "rule_ref", "version")}
    return {**fields, "text": str(text["body"])}


def _fill(content: Any, values: Mapping[str, str], where: str) -> Any:
    if isinstance(content, str):

        def word(match: re.Match[str]) -> str:
            if match.group(1) not in values:
                raise PackError(f"{where}: placeholder {{{match.group(1)}}} has no value in any layer")
            return values[match.group(1)]

        return PLACEHOLDER.sub(word, content)
    if isinstance(content, dict):
        return {k: _fill(v, values, where) for k, v in content.items()}
    if isinstance(content, list):
        return [_fill(v, values, where) for v in content]
    return content


# --- the library: every pack in one or more pack sets -------------------------------------------------------------


class PackLibrary:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        if not roots:
            raise PackError("no pack folder given")
        self.roots = roots
        self.schema_dir = _schema_dir(roots[0])
        self._words = lint_words(roots[0])
        self._raw: dict[str, dict[str, Pack]] = {layer: {} for layer in (*LAYERS, "overlay")}
        for root in roots:
            self._read(root)
        self._packs = {layer: {c: self._resolve(layer, c, ()) for c in self._raw[layer]} for layer in LAYERS}
        self._packs["overlay"] = self._raw["overlay"]
        for layer_packs in self._packs.values():
            for pack in layer_packs.values():
                _validate(pack, self.schema_dir)
        self._check_overlays()
        self._lint()
        self._combined: dict[tuple[str, str, str], MergedPack] = {}
        for combo in self._catalogue():
            self.combine(*combo)

    def _read(self, root: Path) -> None:
        if not root.is_dir():
            raise PackError(f"pack folder {root} does not exist")
        for layer in LAYERS:
            for folder in sorted(p for p in (root / layer).glob("*") if p.is_dir()):
                if folder.name in self._raw[layer]:
                    raise PackError(f"{layer}/{folder.name}: defined in more than one pack folder")
                self._raw[layer][folder.name] = _read_pack(root, layer, folder.name, folder)
        for folder in sorted(p for p in (root / "overlay").glob("*/*") if p.is_dir()):
            code = f"{folder.parent.name}/{folder.name}"
            self._raw["overlay"][code] = _read_pack(root, "overlay", code, folder)

    def _resolve(self, layer: str, code: str, chain: tuple[str, ...]) -> Pack:
        pack = self._raw[layer][code]
        parent_code = pack.meta.get("extends")
        if parent_code is None:
            return pack
        if parent_code in (*chain, code):
            raise PackError(f"{pack.rel('pack.yaml')}: extends loops back to '{parent_code}'")
        if parent_code not in self._raw[layer]:
            raise PackError(f"{pack.rel('pack.yaml')}: extends '{parent_code}', which is not a {layer} pack")
        parent = self._resolve(layer, parent_code, (*chain, code))
        files = {name: copy.deepcopy(content) for name, content in parent.files.items()}
        for name, content in pack.files.items():
            files[name] = _merge(files[name], content) if name in files else content
        meta = {**parent.meta, **pack.meta}
        return Pack(layer, code, pack.root, meta, files, {**parent.sources, **pack.sources}, pack.raw_text)

    def _check_overlays(self) -> None:
        for code, pack in self._packs["overlay"].items():
            jurisdiction, specialty = code.split("/")
            if (pack.meta["jurisdiction"], pack.meta["specialty"]) != (jurisdiction, specialty):
                raise PackError(f"{pack.rel('pack.yaml')}: names a different jurisdiction or specialty than its folder")
            for layer, needed in (("jurisdiction", jurisdiction), ("specialty", specialty)):
                if needed not in self._packs[layer]:
                    raise PackError(f"{pack.rel('pack.yaml')}: no {layer} pack '{needed}'")
        for layer in LAYERS:
            for code, pack in self._packs[layer].items():
                if pack.meta["code"] != code:
                    raise PackError(f"{pack.rel('pack.yaml')}: code '{pack.meta['code']}' differs from its folder")

    def _lint(self) -> None:
        """Jurisdiction and case-type files hold no specialty words; specialty files no country words."""
        specialty_words = self._words.get("specialty_words", []) + list(self._packs["specialty"])
        country_words = self._words.get("country_words", []) + list(self._packs["jurisdiction"])
        rules = {"jurisdiction": ("specialty", specialty_words), "case-type": ("specialty", specialty_words)}
        rules["specialty"] = ("country", country_words)
        for layer, (kind, words) in rules.items():
            for pack in self._raw[layer].values():
                for name, text in pack.raw_text.items():
                    found = find_words(text, words)
                    if found:
                        raise PackError(
                            f"{pack.rel(name)}: {', '.join(found)} is a {kind} word; it belongs in a {kind} "
                            "or overlay pack"
                        )

    def _catalogue(self) -> Iterator[tuple[str, str, str]]:
        """Combinations that make sense together: one pack folder, and the specialty's profession registers there."""
        for j in self._packs["jurisdiction"].values():
            for ct in self._packs["case-type"].values():
                for sp in self._packs["specialty"].values():
                    same_set = j.root == ct.root == sp.root
                    if same_set and sp.meta["profession"] in j.meta["registration_bodies"]:
                        yield (j.code, ct.code, sp.code)

    # public
    def codes(self, layer: str) -> list[str]:
        return sorted(self._packs[layer])

    def pack(self, layer: str, code: str) -> Pack:
        try:
            return self._packs[layer][code]
        except KeyError:
            raise PackError(f"no {layer} pack '{code}'") from None

    def combine(self, jurisdiction: str, case_type: str, specialty: str) -> MergedPack:
        key = (jurisdiction, case_type, specialty)
        if key not in self._combined:
            layers = [self.pack(layer, code) for layer, code in zip(LAYERS, key, strict=True)]
            overlay = self._packs["overlay"].get(f"{jurisdiction}/{specialty}")
            self._combined[key] = MergedPack([*layers, overlay] if overlay else layers)
        return self._combined[key]

    def for_case(self, case: Mapping[str, Any]) -> MergedPack:
        """The merged pack for a case row (its `jurisdiction`, `case_type` and `specialty` codes)."""
        return self.combine(str(case["jurisdiction"]), str(case["case_type"]), str(case["specialty"]))

    def identifier_finders(self) -> list[Callable[[str], list[tuple[int, int, str]]]]:
        """Every loaded jurisdiction's identifier recogniser: the log scrubber removes them all."""
        return [
            _identifier_finder(p.files["identifiers.py"], p.rel("identifiers.py"))
            for _, p in sorted(self._packs["jurisdiction"].items())
        ]

    def jurisdictions_in(self, cell: str) -> list[Pack]:
        return [p for c, p in sorted(self._packs["jurisdiction"].items()) if p.meta["cell"] == cell]

    def combinations_in(self, cell: str) -> list[tuple[Pack, Pack, Pack]]:
        codes = {j.code for j in self.jurisdictions_in(cell)}
        return [
            (self.pack("jurisdiction", j), self.pack("case-type", ct), self.pack("specialty", sp))
            for j, ct, sp in sorted(self._catalogue())
            if j in codes
        ]

    def jurisdiction_summary(self, code: str) -> dict[str, Any]:
        """One jurisdiction on its own: placeholders are left for the case's specialty to fill."""
        pack = self.pack("jurisdiction", code)
        meta, files = pack.meta, pack.files
        legal_tests = files.get("legal_tests.md")
        return {
            "code": code,
            "name": meta["name"],
            "version": meta["version"],
            "timezone": meta["timezone"],
            "date_order": meta["date_order"],
            "locale": meta["locale"],
            "language": meta["language"],
            "currency": meta["currency"],
            "working_week": meta["working_week"],
            "vocabulary": copy.deepcopy(files["vocabulary.yaml"]),
            "declarations": [_declaration(files["declarations.md"])],
            "compliance_checklist": {
                "rule_ref": files["report.yaml"]["rule_ref"],
                "items": _enabled(files["report.yaml"]["required_contents"]),
            },
            "deadline_rules": _enabled(files["deadlines.yaml"]["deadlines"]),
            "legal_tests": legal_tests["body"] if legal_tests else None,
            "court_tracks": list(meta.get("court_tracks", [])),
        }


@cache
def load_library(*roots: Path) -> PackLibrary:
    """Load and check every pack under `roots` (each a pack set). Cached per set of roots."""
    return PackLibrary(tuple(Path(r).resolve() for r in roots))


def default_library() -> PackLibrary:
    """The cell's packs: `PACKS_DIR` (folders separated by the path separator), else the launch packs only."""
    configured = os.environ.get("PACKS_DIR", "")
    roots = [Path(p) for p in configured.split(os.pathsep) if p] or [REPO / "packs"]
    return load_library(*roots)


# --- rule 11 guard: forbidden words in slice code and prompt templates (architecture 3.5) -------------------------

SCANNED = ("api/app/features", "web/src/capabilities", "api/app/core/ai/prompts", "api/app/core/contracts")
SKIPPED_PARTS = frozenset({"node_modules", "__pycache__", "dist", "build", ".pytest_cache"})
BINARY = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico", ".woff", ".woff2", ".pyc", ".zip"})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    word: str


def slice_code_forbidden_words(packs_root: Path) -> list[str]:
    """`slice_code_forbidden` plus every country and specialty word, without repeats."""
    words = lint_words(packs_root)
    keys = ("slice_code_forbidden", "country_words", "specialty_words")
    return list(dict.fromkeys(w for k in keys for w in words.get(k, [])))


def slice_code_files(repo: Path) -> list[Path]:
    files = []
    for folder in SCANNED:
        for path in sorted((repo / folder).rglob("*")):
            if path.is_file() and path.suffix not in BINARY and not SKIPPED_PARTS & set(path.parts):
                files.append(path)
    return files


def scan_slice_code(repo: Path) -> list[Finding]:
    words = slice_code_forbidden_words(repo / "packs")
    findings: list[Finding] = []
    for path in slice_code_files(repo):
        rel = path.relative_to(repo).as_posix()
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            findings.extend(Finding(rel, number, w) for w in find_words(line, words))
    return findings


def main(argv: list[str] | None = None) -> int:
    """CI check: load every pack under the given folders (default: the cell's packs)."""
    args = sys.argv[1:] if argv is None else argv
    try:
        library = load_library(*map(Path, args)) if args else default_library()
    except PackError as exc:
        print(f"pack check failed: {exc}", file=sys.stderr)
        return 1
    combos = sum(1 for _ in library._catalogue())
    print(f"packs ok: {', '.join(str(r) for r in library.roots)} ({combos} combinations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

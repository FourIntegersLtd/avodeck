"""Migrations: one version per number, each slice inside its own range, every change can be undone.

Supabase records a migration by its number alone, so two files sharing a number break `db reset`
for everyone. Slices build in parallel, so each owns a block of numbers (AGENTS.md, Migration
ranges); the word after the number names the slice. Every migration has a down file.

They also run in number order on an empty database, so a migration can only touch a table an
earlier one has already created: an alter in a low range against a table built in a high one passes
every test on a database that already has the table, and breaks every fresh one.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "supabase" / "migrations"
NAME = re.compile(r"^(\d{4})_([a-z]+(?:_serving)?)_.+\.sql$")
CREATES = re.compile(r"create table (?:if not exists )?app\.([a-z_0-9]+)")
ALTERS = re.compile(r"alter table (?:if exists )?(?:only )?app\.([a-z_0-9]+)")

RANGES = {
    "foundation": range(0, 100),
    "pipeline": range(100, 200),
    "reader": range(200, 300),
    "setup": range(300, 400),
    "understand": range(400, 500),
    "write": range(500, 600),
    "report": range(600, 700),
    "after_serving": range(700, 800),
    "close": range(800, 900),
    "trial": range(900, 1000),
    "organisations": range(1000, 1100),
}


def _ups() -> list[Path]:
    return sorted(p for p in MIGRATIONS.glob("*.sql"))


def test_every_migration_is_named_number_then_slice() -> None:
    bad = [p.name for p in _ups() if not NAME.match(p.name) or NAME.match(p.name)[2] not in RANGES]  # type: ignore[index]
    assert bad == [], f"name migrations NNNN_<slice>_<what>.sql: {bad}"


def test_no_two_migrations_share_a_number() -> None:
    counts = Counter(p.name[:4] for p in _ups())
    assert [n for n, c in counts.items() if c > 1] == []


def test_each_slice_numbers_inside_its_own_range() -> None:
    outside = []
    for p in _ups():
        m = NAME.match(p.name)
        if m and m[2] in RANGES and int(m[1]) not in RANGES[m[2]]:
            outside.append(p.name)
    assert outside == [], f"outside the slice's range (AGENTS.md): {outside}"


def test_every_migration_has_a_down_file() -> None:
    missing = [p.name for p in _ups() if not (MIGRATIONS / "down" / p.name).is_file()]
    assert missing == []


def _created_at() -> dict[str, int]:
    """Each `app` table with the number of the migration that creates it."""
    where: dict[str, int] = {}
    for p in _ups():
        for table in CREATES.findall(p.read_text()):
            where.setdefault(table, int(p.name[:4]))
    return where


def test_no_migration_alters_a_table_a_later_one_creates() -> None:
    created = _created_at()
    early = []
    for p in _ups():
        number = int(p.name[:4])
        for table in sorted(set(ALTERS.findall(p.read_text()))):
            if created.get(table, number) > number:
                early.append(f"{p.name} alters app.{table}, created in {created[table]:04d}")
    assert early == [], f"a fresh database runs these in order: {early}"

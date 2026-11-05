"""The connection budget in `docs/runbooks/railway.md` adds up and agrees with `railway/*.json` (scalability PR 16).

Every API process holds a pool of `API_DB_POOL_MAX` connections and one listener; every worker holds its own
pool and the queue library's. Per pooler role the total must stay under that role's pooler pool size, and the
sum over both roles at least 10 under the database's direct `max_connections`. If a replica count, a process
count or a pool size changes in one place and not the other, this fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = (REPO / "docs" / "runbooks" / "railway.md").read_text()
DIRECT_MAX_CONNECTIONS = 80  # Supabase Small: 90 direct, 10 kept free
DEFAULT_WORKER_POOL, DEFAULT_QUEUE_POOL = 10, 4  # api/app/core/config.py


def replicas(name: str) -> int:
    deploy = json.loads((REPO / "railway" / f"{name}.json").read_text())["deploy"]
    return sum(region["numReplicas"] for region in deploy["multiRegionConfig"].values())


def start_command(name: str) -> str:
    return json.loads((REPO / "railway" / f"{name}.json").read_text())["deploy"].get("startCommand", "")


def env_in(command: str, name: str, default: int) -> int:
    match = re.search(rf"\b{name}=(\d+)", command)
    return int(match.group(1)) if match else default


def budget_rows() -> dict[str, list[int | str]]:
    """The connection-budget table: service -> [role, replicas, processes, pool, queue pool, listener, total]."""
    section = RUNBOOK.split("### Connection budget", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, list[int | str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not cells[0].startswith("`"):
            continue
        service = cells[0].strip("`").split("`")[0]
        shape = re.fullmatch(r"(\d+) x (\d+)", cells[2])
        assert shape, f"{service}: replicas x processes should read '2 x 2'"
        rows[service] = [cells[1].strip("`"), int(shape[1]), int(shape[2]), *map(int, cells[3:])]
    return rows


def api_variable(name: str) -> int:
    match = re.search(rf"`{name}=(\d+)`", RUNBOOK.split("### Connection budget", 1)[1])
    assert match, f"the connection budget should state `{name}=<number>` for the api service"
    return int(match.group(1))


def test_every_row_adds_up_from_its_own_columns() -> None:
    rows = budget_rows()
    assert {"api", "worker", "worker-interactive"} <= set(rows)
    for service, (_role, reps, procs, pool, queue, listener, total) in rows.items():
        assert total == reps * procs * (pool + queue + listener), service


def test_the_runbook_rows_match_the_railway_files() -> None:
    rows = budget_rows()
    assert rows["api"][1] == replicas("api") == 2
    for service in ("worker", "worker-interactive"):
        _role, reps, procs, pool, queue, listener, _total = rows[service]
        command = start_command(service)
        assert (reps, procs, listener) == (replicas(service), 1, 0), service
        assert pool == env_in(command, "WORKER_DB_POOL_MAX", DEFAULT_WORKER_POOL), service
        assert queue == env_in(command, "WORKER_QUEUE_POOL_MAX", DEFAULT_QUEUE_POOL), service


def test_the_api_row_matches_the_variables_the_runbook_tells_the_service_to_set() -> None:
    _role, _reps, procs, pool, queue, listener, _total = budget_rows()["api"]
    assert procs == api_variable("WEB_CONCURRENCY") == 2
    assert pool == api_variable("API_DB_POOL_MAX") == 8
    assert (queue, listener) == (0, 1)  # one LISTEN connection per process, none from the queue library


def test_the_settings_table_states_the_api_replica_count() -> None:
    row = next(line for line in RUNBOOK.splitlines() if line.startswith("| Replicas |"))
    assert row.split("|")[2].strip() == str(replicas("api"))


def test_each_role_total_is_the_sum_of_its_rows_and_fits_the_poolers_pool_size() -> None:
    rows = budget_rows()
    for role, services in (("app_api", ["api"]), ("app_worker", ["worker", "worker-interactive"])):
        total = sum(int(rows[s][6]) for s in services)
        match = re.search(rf"\*\*`{role}` total[^|]*\|[^*]*\*\*(\d+)\*\* \(pooler pool size at least (\d+)\)", RUNBOOK)
        assert match, f"{role} total row not found"
        stated, pooler = int(match[1]), int(match[2])
        assert stated == total, role
        assert pooler >= total, role
        assert all(rows[s][0] == role for s in services), role


def test_both_roles_together_stay_under_the_databases_direct_limit() -> None:
    rows = budget_rows()
    assert sum(int(row[6]) for row in rows.values()) <= DIRECT_MAX_CONNECTIONS
    assert re.search(r"Final total (\d+) \+ (\d+) = (\d+)", RUNBOOK)
    first, second, both = map(int, re.search(r"Final total (\d+) \+ (\d+) = (\d+)", RUNBOOK).groups())  # type: ignore[union-attr]
    assert first + second == both == sum(int(row[6]) for row in rows.values())

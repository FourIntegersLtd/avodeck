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
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = (REPO / "docs" / "runbooks" / "railway.md").read_text()
DIRECT_MAX_CONNECTIONS = 80  # Supabase Small: 90 direct, 10 kept free
DEFAULT_WORKER_POOL, DEFAULT_QUEUE_POOL = 10, 4  # api/app/core/config.py


class Row(NamedTuple):
    role: str
    replicas: int
    processes: int
    pool: int
    queue: int
    listener: int
    total: int


def deploy_of(name: str) -> dict[str, Any]:
    deploy: dict[str, Any] = json.loads((REPO / "railway" / f"{name}.json").read_text())["deploy"]
    return deploy


def replicas(name: str) -> int:
    return int(sum(region["numReplicas"] for region in deploy_of(name)["multiRegionConfig"].values()))


def start_command(name: str) -> str:
    return str(deploy_of(name).get("startCommand", ""))


def env_in(command: str, name: str, default: int) -> int:
    match = re.search(rf"\b{name}=(\d+)", command)
    return int(match.group(1)) if match else default


def budget_rows() -> dict[str, Row]:
    """The connection-budget table: service -> Row."""
    section = RUNBOOK.split("### Connection budget", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, Row] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not cells[0].startswith("`"):
            continue
        service = cells[0].strip("`").split("`")[0]
        shape = re.fullmatch(r"(\d+) x (\d+)", cells[2])
        assert shape, f"{service}: replicas x processes should read '2 x 2'"
        rows[service] = Row(cells[1].strip("`"), int(shape[1]), int(shape[2]), *map(int, cells[3:]))
    return rows


def api_variable(name: str) -> int:
    match = re.search(rf"`{name}=(\d+)`", RUNBOOK.split("### Connection budget", 1)[1])
    assert match, f"the connection budget should state `{name}=<number>` for the api service"
    return int(match.group(1))


def test_every_row_adds_up_from_its_own_columns() -> None:
    rows = budget_rows()
    assert {"api", "worker", "worker-interactive"} <= set(rows)
    for service, row in rows.items():
        assert row.total == row.replicas * row.processes * (row.pool + row.queue + row.listener), service


def test_the_runbook_rows_match_the_railway_files() -> None:
    rows = budget_rows()
    assert rows["api"].replicas == replicas("api") == 2
    for service in ("worker", "worker-interactive"):
        row = rows[service]
        command = start_command(service)
        assert (row.replicas, row.processes, row.listener) == (replicas(service), 1, 0), service
        assert row.pool == env_in(command, "WORKER_DB_POOL_MAX", DEFAULT_WORKER_POOL), service
        assert row.queue == env_in(command, "WORKER_QUEUE_POOL_MAX", DEFAULT_QUEUE_POOL), service


def test_the_api_row_matches_the_variables_the_runbook_tells_the_service_to_set() -> None:
    row = budget_rows()["api"]
    assert row.processes == api_variable("WEB_CONCURRENCY") == 2
    assert row.pool == api_variable("API_DB_POOL_MAX") == 8
    assert (row.queue, row.listener) == (0, 1)  # one LISTEN connection per process, none from the queue library


def test_the_settings_table_states_the_api_replica_count() -> None:
    row = next(line for line in RUNBOOK.splitlines() if line.startswith("| Replicas |"))
    assert row.split("|")[2].strip() == str(replicas("api"))


def test_each_role_total_is_the_sum_of_its_rows_and_fits_the_poolers_pool_size() -> None:
    rows = budget_rows()
    for role, services in (("app_api", ["api"]), ("app_worker", ["worker", "worker-interactive"])):
        total = sum(rows[s].total for s in services)
        match = re.search(rf"\*\*`{role}` total[^|]*\|[^*]*\*\*(\d+)\*\* \(pooler pool size at least (\d+)\)", RUNBOOK)
        assert match, f"{role} total row not found"
        stated, pooler = int(match[1]), int(match[2])
        assert stated == total, role
        assert pooler >= total, role
        assert all(rows[s].role == role for s in services), role


def test_both_roles_together_stay_under_the_databases_direct_limit() -> None:
    rows = budget_rows()
    everything = sum(row.total for row in rows.values())
    assert everything <= DIRECT_MAX_CONNECTIONS
    stated = re.search(r"Final total (\d+) \+ (\d+) = (\d+)", RUNBOOK)
    assert stated, "the runbook should state 'Final total A + B = C'"
    first, second, both = map(int, stated.groups())
    assert first + second == both == everything

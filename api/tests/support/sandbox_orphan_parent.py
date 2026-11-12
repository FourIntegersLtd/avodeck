"""A parent process for one test: it starts the sandbox, prints its child's pid, then sits in a long call.

The test kills this process with SIGKILL and checks that the child does not outlive it (audit 13 review, F-f).
"""

from __future__ import annotations

import asyncio

from app.core import sandbox
from app.core.config import SandboxSettings
from tests.support import sandbox_fns as fns


async def main() -> None:
    await sandbox.start(
        SandboxSettings(pool_size=1, timeout_seconds=300.0, memory_bytes=512 * 1024 * 1024, recycle_after=50)
    )
    print(await sandbox.run(fns.pid), flush=True)
    await sandbox.run(fns.sleep_for, 250)


if __name__ == "__main__":
    asyncio.run(main())

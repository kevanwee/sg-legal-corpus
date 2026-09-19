"""Stage 1 -- fetch. The only stage that touches the network.

Puts raw responses on disk without interpreting them, checkpointed per work
unit so a crash at 80% resumes at 80%.
"""

from __future__ import annotations

import logging
from datetime import date

from ..adapters import registry
from ..checkpoint import Checkpoint
from ..config import POLICIES, Paths
from ..net.cache import SnapshotStore
from ..net.client import Client, RateLimitExceeded, RobotsDisallowed

log = logging.getLogger(__name__)


def run(
    adapter_name: str,
    paths: Paths,
    *,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
    min_interval: float | None = None,
    options: dict[str, object] | None = None,
) -> dict[str, int]:
    adapter = registry.get(adapter_name)
    adapter.configure(snapshot_root=paths.snapshots, **(options or {}))
    paths.ensure()

    policy = POLICIES[adapter_name].merge(min_interval)
    store = SnapshotStore(paths.snapshots)
    checkpoint = Checkpoint(paths.checkpoints / f"{adapter_name}.jsonl")

    fetched = skipped = empty = failures = 0

    with Client(policy) as client:
        for unit in adapter.plan(since, until):
            if limit is not None and fetched >= limit:
                break
            if checkpoint.is_done(unit.key):
                skipped += 1
                continue

            try:
                snapshots = list(adapter.fetch(unit, client))
            except RobotsDisallowed as exc:
                log.warning("robots.txt disallows %s; skipping", exc)
                checkpoint.mark_empty(unit.key, "robots_disallowed")
                continue
            except RateLimitExceeded as exc:
                log.error("%s", exc)
                break
            except Exception as exc:
                failures += 1
                log.error("fetch failed for %s (left pending): %s", unit.key, exc)
                continue

            if not snapshots:
                # Cache the negative result. Without this, a non-sitting day and
                # a failed fetch look identical on the next run.
                checkpoint.mark_empty(unit.key, "no_data")
                empty += 1
                continue

            for url, body, status, params in snapshots:
                store.put(
                    adapter=adapter_name,
                    url=url,
                    body=body,
                    status=status,
                    params=params,
                )
            checkpoint.mark(unit.key)
            fetched += 1
            log.info("fetched %s (%d snapshot(s))", unit.key, len(snapshots))

    return {
        "fetched": fetched,
        "failures": failures,
        "skipped": skipped,
        "empty": empty,
        "requests": client.request_count,
        "snapshots_total": store.count(adapter_name),
    }

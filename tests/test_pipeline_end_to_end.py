"""Phase 0 gate: Hansard runs snapshot -> normalise -> index -> search offline.

If this passes, the record model, the URN scheme, the snapshot store, the
adapter protocol and the FTS index agree with each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from sgcorpus.config import Paths
from sgcorpus.net.cache import SnapshotStore
from sgcorpus.pipeline import index as index_stage
from sgcorpus.pipeline import normalise as normalise_stage
from sgcorpus.store import sqlite

FIXTURE = Path(__file__).parent / "fixtures" / "hansard_2024-02-06.json"


def test_fixture_to_searchable_index(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.ensure()

    # Stand in for `fetch`: put the fixture into the snapshot store directly.
    store = SnapshotStore(paths.snapshots)
    snapshot = store.put(
        adapter="hansard",
        url="https://sprs.parl.gov.sg/search/getHansardReport/?sittingDate=06-02-2024",
        body=FIXTURE.read_bytes(),
        status=200,
        params={"sittingDate": "06-02-2024"},
    )

    normalised = normalise_stage.run("hansard", paths)
    assert normalised["failures"] == 0
    assert normalised["documents"] == 12  # 1 sitting + 3 sections + 8 speeches

    indexed = index_stage.run(paths, adapters=["hansard"])
    assert indexed["documents"] == 12

    conn = sqlite.connect(paths.db, read_only=True)

    hits = sqlite.search(conn, "notifiable data breach", corpus=["hansard"])
    assert hits, "expected the minister's speech to be retrievable"
    assert hits[0]["urn"].startswith("urn:sg:hansard:2024-02-06:")

    # Every hit resolves to a real record carrying its provenance.
    document = sqlite.get_document(conn, hits[0]["urn"])
    assert document is not None
    provenance = json.loads(document["provenance"])
    assert provenance["snapshot_sha256"] == snapshot.sha256
    assert provenance["source_url"].startswith("https://sprs.parl.gov.sg/")

    conn.close()


def test_snapshot_store_is_content_addressed(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    body = FIXTURE.read_bytes()

    first = store.put(adapter="hansard", url="https://example.test/a", body=body, status=200)
    second = store.put(adapter="hansard", url="https://example.test/a", body=body, status=200)

    # Identical bytes, one file: a re-fetch of unchanged content costs nothing.
    assert first.sha256 == second.sha256
    assert store.count("hansard") == 1

    recovered = store.get("hansard", first.sha256)
    assert recovered is not None
    assert recovered.body == body


def test_checkpoint_resumes(tmp_path: Path) -> None:
    from sgcorpus.checkpoint import Checkpoint

    path = tmp_path / "hansard.jsonl"
    first = Checkpoint(path)
    first.mark("hansard:2024-02-06")
    first.mark_empty("hansard:2024-02-07", "no_data")

    # A fresh process sees both, so an interrupted run does not repeat work --
    # including the work that legitimately produced nothing.
    resumed = Checkpoint(path)
    assert resumed.is_done("hansard:2024-02-06")
    assert resumed.is_done("hansard:2024-02-07")
    assert not resumed.is_done("hansard:2024-02-08")

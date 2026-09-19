"""Content-addressed snapshot store.

Raw response bytes, named by the SHA-256 of the body, with a sidecar manifest
recording where they came from. Immutable: every later stage is a pure function
of this directory. See docs/adr/0003-snapshots-are-immutable.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Snapshot:
    sha256: str
    body: bytes
    url: str
    retrieved_at: datetime
    status: int
    params: dict[str, Any]
    headers: dict[str, str]
    adapter: str

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, adapter: str, digest: str) -> tuple[Path, Path]:
        # Two-level fan-out keeps directory listings usable at corpus scale.
        bucket = self.root / adapter / digest[:2] / digest[2:4]
        return bucket / f"{digest}.bin", bucket / f"{digest}.json"

    def put(
        self,
        *,
        adapter: str,
        url: str,
        body: bytes,
        status: int,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Snapshot:
        digest = hashlib.sha256(body).hexdigest()
        blob_path, manifest_path = self._paths(adapter, digest)
        retrieved_at = datetime.now(UTC)

        snapshot = Snapshot(
            sha256=digest,
            body=body,
            url=url,
            retrieved_at=retrieved_at,
            status=status,
            params=params or {},
            headers=headers or {},
            adapter=adapter,
        )

        # Identical bytes produce a digest that already exists, so a re-fetch of
        # unchanged content costs nothing and is immediately detectable.
        if blob_path.exists():
            return snapshot

        blob_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = blob_path.with_suffix(".bin.tmp")
        tmp.write_bytes(body)
        tmp.replace(blob_path)

        manifest_path.write_text(
            json.dumps(
                {
                    "sha256": digest,
                    "url": url,
                    "retrieved_at": retrieved_at.isoformat(),
                    "status": status,
                    "params": params or {},
                    "headers": headers or {},
                    "adapter": adapter,
                    "bytes": len(body),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return snapshot

    def get(self, adapter: str, digest: str) -> Snapshot | None:
        blob_path, manifest_path = self._paths(adapter, digest)
        if not blob_path.exists() or not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return Snapshot(
            sha256=digest,
            body=blob_path.read_bytes(),
            url=manifest["url"],
            retrieved_at=datetime.fromisoformat(manifest["retrieved_at"]),
            status=manifest["status"],
            params=manifest.get("params", {}),
            headers=manifest.get("headers", {}),
            adapter=adapter,
        )

    def iter_snapshots(self, adapter: str) -> Iterator[Snapshot]:
        """Every snapshot for an adapter, in stable hash order.

        This is the input to ``normalise``: a parser fix costs a re-run over
        local disk, not a re-crawl.
        """
        base = self.root / adapter
        if not base.exists():
            return
        for manifest_path in sorted(base.rglob("*.json")):
            snapshot = self.get(adapter, manifest_path.stem)
            if snapshot is not None:
                yield snapshot

    def count(self, adapter: str) -> int:
        base = self.root / adapter
        return sum(1 for _ in base.rglob("*.json")) if base.exists() else 0

"""sgcorpus command line.

Every MCP tool has a CLI equivalent, deliberately: a retrieval bug should be
reproducible with a shell command, without an agent in the loop.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .adapters import registry
from .config import POLICIES, default_paths
from .pipeline import index as index_stage
from .pipeline import ingest as ingest_stage
from .pipeline import normalise as normalise_stage
from .store import sqlite

app = typer.Typer(
    help="Singapore legal corpus: fetch, normalise, index and serve.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )


@app.command()
def sources() -> None:
    """List the adapters and their politeness policies."""
    table = Table("adapter", "corpus", "authority", "status", "rate", "spec")
    for name, adapter in registry.all_adapters().items():
        built = name in registry.implemented()
        policy = POLICIES.get(name)
        table.add_row(
            name,
            str(adapter.corpus),
            str(adapter.authority),
            "[green]implemented[/]" if built else "[yellow]planned[/]",
            f"1 req / {policy.min_interval:g}s" if policy else "-",
            getattr(adapter, "spec_doc", ""),
        )
    console.print(table)


@app.command()
def fetch(
    adapter: Annotated[str, typer.Argument(help="Adapter name; see `sgcorpus sources`.")],
    start: Annotated[str | None, typer.Option(help="ISO date, inclusive.")] = None,
    end: Annotated[str | None, typer.Option(help="ISO date, inclusive.")] = None,
    limit: Annotated[int | None, typer.Option(help="Stop after N work units.")] = None,
    min_interval: Annotated[
        float | None,
        typer.Option(help="Slow the request rate. Cannot be used to speed it up."),
    ] = None,
    vernacular: Annotated[
        bool,
        typer.Option(
            help="hansard: also fetch Malay/Mandarin/Tamil speech PDFs. "
            "Roughly 8 extra requests per sitting."
        ),
    ] = False,
    versions: Annotated[bool, typer.Option(help="sso: fetch all enumerated historical versions.")] = False,
    include_sl: Annotated[bool, typer.Option(help="sso: include current subsidiary legislation.")] = False,
    include_repealed: Annotated[bool, typer.Option(help="sso: include repealed Acts.")] = False,
    verbose: bool = False,
) -> None:
    """Fetch raw snapshots. The only command that touches the network."""
    _setup_logging(verbose)
    result = ingest_stage.run(
        adapter,
        default_paths(),
        since=date.fromisoformat(start) if start else None,
        until=date.fromisoformat(end) if end else None,
        limit=limit,
        min_interval=min_interval,
        options={"vernacular": vernacular, "versions": versions,
                 "include_sl": include_sl, "include_repealed": include_repealed},
    )
    console.print(result)


@app.command()
def normalise(
    adapter: Annotated[str, typer.Argument(help="Adapter name.")],
    verbose: bool = False,
) -> None:
    """Parse snapshots into canonical Document JSONL. Offline."""
    _setup_logging(verbose)
    console.print(normalise_stage.run(adapter, default_paths()))


@app.command()
def index(
    adapter: Annotated[list[str] | None, typer.Option(help="Limit to these adapters.")] = None,
    verbose: bool = False,
) -> None:
    """Build the SQLite/FTS5 artifact from the document JSONL."""
    _setup_logging(verbose)
    console.print(index_stage.run(default_paths(), adapters=adapter))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="FTS5 query.")],
    corpus: Annotated[list[str] | None, typer.Option(help="Restrict to these corpora.")] = None,
    limit: int = 10,
) -> None:
    """Full-text search the built index."""
    paths = default_paths()
    if not paths.db.exists():
        console.print("[red]No index.[/] Run `sgcorpus index` first.")
        raise typer.Exit(1)

    conn = sqlite.connect(paths.db, read_only=True)
    hits = sqlite.search(conn, query, corpus=corpus, limit=limit)
    conn.close()

    if not hits:
        console.print(f"No hits for {query!r}. `sgcorpus stats` shows what is covered.")
        return

    table = Table("urn", "citation", "issued", "snippet")
    for hit in hits:
        table.add_row(hit["urn"], hit["citation"] or "", hit["issued"] or "", hit["snippet"] or "")
    console.print(table)


@app.command()
def stats() -> None:
    """Coverage and freshness per corpus."""
    paths = default_paths()
    if not paths.db.exists():
        console.print("[red]No index.[/] Run `sgcorpus index` first.")
        raise typer.Exit(1)

    conn = sqlite.connect(paths.db, read_only=True)
    rows = sqlite.stats(conn)
    conn.close()

    table = Table("corpus", "documents", "earliest", "latest", "adapter", "rev", "quality")
    for row in rows:
        quality = json.loads(row["quality"] or "{}")
        table.add_row(
            row["corpus"],
            str(row["document_count"]),
            row["earliest"] or "",
            row["latest"] or "",
            row["adapter_version"] or "",
            str(row["parser_rev"] or ""),
            "  ".join(f"{k}={v}" for k, v in quality.items()) or "-",
        )
    console.print(table)


@app.command()
def serve() -> None:
    """Run the MCP server over stdio against the built index."""
    from .mcp.server import main as serve_main

    serve_main()


if __name__ == "__main__":
    app()

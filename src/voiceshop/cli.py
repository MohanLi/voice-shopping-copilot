"""Command line: ``voiceshop <command>``.

    voiceshop ingest          raw Kaggle CSV -> data/processed/products.parquet
    voiceshop enrich          optional LLM attribute extraction -> data/derived/
    voiceshop index           embeddings + Qdrant + BM25 -> data/index/
    voiceshop search "…"      query the index directly (no LLM)
    voiceshop config          effective configuration, secrets masked

Further commands (mcp, ask, voice, serve, eval) are added by the modules that implement them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from voiceshop.config import get_settings

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Voice Shopping Copilot")
console = Console()


def _logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def config() -> None:
    """Show the effective configuration (secrets are reported as set/missing)."""
    console.print_json(json.dumps(get_settings().public_dict(), default=str))


@app.command()
def ingest(
    raw_csv: Annotated[Path | None, typer.Option(help="Raw Kaggle CSV (default: data/raw/amazon_product_dataset_2020.csv)")] = None,
    no_llm_attributes: Annotated[bool, typer.Option(help="Ignore the cached LLM attribute extraction")] = False,
) -> None:
    """Clean the raw catalog into data/processed/products.parquet and write the ingestion report."""
    from voiceshop.data import ingest as ing

    _logging()
    s = get_settings()
    src = raw_csv or (s.data_dir / "raw" / ing.RAW_CSV_NAME)
    if not src.exists():
        console.print(f"[red]Raw dataset not found:[/red] {src}\nRun:  python scripts/download_assets.py --only data")
        raise typer.Exit(1)
    report = ing.run(src, s.data_dir / "processed", None if no_llm_attributes else s.data_dir / "derived")
    console.print(f"[green]products.parquet[/green]: kept {report['rows_kept']:,} of {report['rows_in']:,} rows")
    console.print(f"dropped: {report['rows_dropped']}")
    table = Table("field", "coverage %")
    for field, pct in report["coverage_pct"].items():
        table.add_row(field, f"{pct:.1f}")
    console.print(table)
    console.print(f"report: {s.data_dir / 'processed' / 'ingest_report.json'}")


@app.command()
def enrich(
    limit: Annotated[int | None, typer.Option(help="Only extract this many missing products")] = None,
    max_spend: Annotated[float, typer.Option(help="Stop after spending this many USD in this run")] = 3.0,
    concurrency: Annotated[int, typer.Option(help="Parallel model calls")] = 8,
) -> None:
    """Extract brand / product type / spoken name / materials with the LLM (cached, resumable)."""
    from voiceshop.data import enrich as en
    from voiceshop.data.derived import DERIVED_FILE
    from voiceshop.data.ingest import load_products

    _logging()
    s = get_settings()
    products = s.data_dir / "processed" / "products.parquet"
    if not products.exists():
        console.print("[red]Run `voiceshop ingest` first.[/red]")
        raise typer.Exit(1)
    rows = load_products(products).to_dict(orient="records")
    stats = en.enrich(rows, s.data_dir / "derived" / DERIVED_FILE, limit=limit, max_spend_usd=max_spend, concurrency=concurrency)
    console.print_json(json.dumps(stats))
    console.print("Run `voiceshop ingest` again to overlay the extracted attributes.")


@app.command()
def index(
    backend: Annotated[str | None, typer.Option(help="fastembed (default from EMBEDDING_BACKEND) or hashing")] = None,
) -> None:
    """Build the search index: embeddings in Qdrant, BM25, vocabulary and manifest."""
    from voiceshop.retrieval.embedder import build_embedder
    from voiceshop.retrieval.engine import build_index

    _logging()
    s = get_settings()
    embedder = build_embedder(s, backend=backend) if backend else None
    manifest = build_index(s, embedder=embedder)
    console.print_json(json.dumps(manifest))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What to look for")],
    top_k: Annotated[int, typer.Option("--top-k", "-k")] = 5,
    max_price: Annotated[float | None, typer.Option(help="Budget in USD")] = None,
    category: Annotated[str | None, typer.Option()] = None,
    brand: Annotated[str | None, typer.Option()] = None,
    age: Annotated[float | None, typer.Option(help="Age in years of the person it is for")] = None,
    material: Annotated[list[str] | None, typer.Option(help="Material(s), any of")] = None,
    mode: Annotated[str, typer.Option(help="hybrid | dense | lexical")] = "hybrid",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Query the index directly, without any LLM (useful to inspect retrieval)."""
    from voiceshop.retrieval.engine import SearchEngine
    from voiceshop.retrieval.filters import SearchFilters

    _logging()
    engine = SearchEngine.load()
    try:
        filters = SearchFilters(price_max=max_price, category=category, brand=brand, age_years=age, materials=material or [])
        res = engine.search(query, filters, top_k=top_k, mode=mode)  # type: ignore[arg-type]
    finally:
        engine.close()
    if as_json:
        console.print_json(res.model_dump_json())
        return
    console.print(f"filters applied: {res.applied_filters or '-'}   relaxed: {res.relaxed or '-'}   "
                  f"unmatched: {res.unmatched_filters or '-'}   strict matches: {res.strict_matches:,}   {res.timings_ms}")
    table = Table("#", "doc_id", "price", "brand", "title", "age", "notes")
    for i, h in enumerate(res.results, 1):
        table.add_row(str(i), h.doc_id, f"${h.price:.2f}", h.brand or "-", h.title[:60], h.age_label or "-", "; ".join(h.constraint_notes) or "-")
    console.print(table)


if __name__ == "__main__":
    app()

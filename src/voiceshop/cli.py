"""Command line: ``voiceshop <command>``.

    voiceshop ingest          raw Kaggle CSV -> data/processed/products.parquet
    voiceshop enrich          optional LLM attribute extraction -> data/derived/
    voiceshop index           embeddings + Qdrant + BM25 -> data/index/
    voiceshop search "…"      query the index directly (no LLM)
    voiceshop mcp serve       start the MCP server (stdio or streamable HTTP)
    voiceshop mcp tools       connect as a client and print what discovery returns
    voiceshop mcp call …      call rag.search / web.search through MCP
    voiceshop ask "…"         one text turn through the full agent workflow (add more quoted turns for a conversation)
    voiceshop eval retrieval  retrieval benchmark and ablation table
    voiceshop config          effective configuration, secrets masked

Further commands (voice, serve) are added by the modules that implement them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

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


def _print_step(event: dict[str, Any]) -> None:
    mark = {"ok": "[green]ok[/green]", "warn": "[yellow]note[/yellow]", "error": "[red]error[/red]"}[event["status"]]
    took = f"{event['duration_ms']:.0f} ms" if event.get("duration_ms") is not None else ""
    console.print(f"  [dim]{event['title']:<28}[/dim] {mark:<5} {event['summary']} [dim]{took}[/dim]", highlight=False)


def _print_turn(result: Any) -> None:
    answer = result.answer
    console.print(f"\n[bold magenta]Assistant (spoken, {len(answer.spoken.split())} words):[/bold magenta] {answer.spoken}", highlight=False)
    if answer.text and answer.text != answer.spoken and not answer.table:
        console.print(f"[dim]on screen:[/dim] {answer.text}", highlight=False)
    if answer.table:
        table = Table("#", "Product", "Price", "Rating", "Ingredients", "Source", "Citation", show_lines=True)
        for row in answer.table:
            table.add_row(str(row.rank), row.product[:48], row.price, row.rating, row.ingredients[:28], row.source, row.citation[:52])
        console.print(table)
    for note in answer.notes:
        console.print(f"  [dim]- {note}[/dim]", highlight=False)
    speech = result.speech or {}
    if speech.get("audio"):
        extra = f", {len(speech['dropped_sentences'])} closing sentence(s) on screen only" if speech.get("dropped_sentences") else ""
        console.print(f"[dim]audio: {speech['audio']} ({speech['duration_s']:.1f} s at speed {speech['speed']:g}{extra})[/dim]", highlight=False)
    console.print(f"[dim]{answer.kind} | grounded by {answer.grounded_by or '-'} | {result.seconds:.1f} s | "
                  f"${result.cost_usd:.4f} | tools: {', '.join(result.tools_called) or 'none'}[/dim]", highlight=False)


def _run_turns(inputs: list[str], *, audio: bool, speak: bool, as_json: bool, quiet: bool) -> None:
    """Shared by ``ask`` (typed turns) and ``voice`` (recordings): run the turns of one conversation and print them."""
    import asyncio

    from voiceshop.assistant import Assistant
    from voiceshop.mcp_client import MCPConnectionError

    async def run() -> None:
        assistant = Assistant.from_settings(voice=True) if (audio or speak) else Assistant()
        async with assistant:
            for item in inputs:
                result = None
                if not as_json and not audio:
                    console.print(f"\n[bold cyan]You:[/bold cyan] {item}")
                events = assistant.stream_audio(Path(item), thread_id="cli") if audio else assistant.stream(item, thread_id="cli")
                async for event in events:
                    if as_json:
                        pass
                    elif event["type"] == "transcript":
                        heard = event["transcript"]
                        console.print(f"\n[bold cyan]You said ({Path(item).name}):[/bold cyan] {heard['text'] or '[nothing heard]'}", highlight=False)
                    elif event["type"] == "step" and not quiet:
                        _print_step(event)
                    if event["type"] == "result":
                        result = event["result"]
                assert result is not None
                if as_json:
                    console.print_json(json.dumps(result.to_dict(include_evidence=False), default=str))
                else:
                    _print_turn(result)

    _logging()
    logging.getLogger().setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except MCPConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def ask(
    turns: Annotated[list[str], typer.Argument(help="What the shopper says. Several arguments make a multi-turn conversation.")],
    speak: Annotated[bool, typer.Option("--speak", help="Also synthesize the spoken reply (audio is kept in logs/audio/)")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full turn record instead of the readable view")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Hide the agent step log")] = False,
) -> None:
    """Run text turns through the whole workflow: guard, router, planner, retriever (MCP), answerer, critic."""
    _run_turns(turns, audio=False, speak=speak, as_json=as_json, quiet=quiet)


@app.command()
def voice(
    recordings: Annotated[list[Path], typer.Argument(help="Audio files (WAV, MP3, M4A, WebM...). Several files make a multi-turn conversation.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the full turn record instead of the readable view")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Hide the agent step log")] = False,
) -> None:
    """Voice to voice: recognise each recording, run the agents, and speak the reply (audio is kept in logs/audio/)."""
    missing = [str(path) for path in recordings if not path.exists()]
    if missing:
        console.print(f"[red]No such file: {', '.join(missing)}[/red]")
        raise typer.Exit(1)
    _run_turns([str(path) for path in recordings], audio=True, speak=True, as_json=as_json, quiet=quiet)


@app.command()
def transcribe(
    recording: Annotated[Path, typer.Argument(help="Audio file to transcribe")],
    words: Annotated[bool, typer.Option("--words", help="Also show word timestamps")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the transcript record")] = False,
) -> None:
    """Speech to text with timestamps and confidence (the ASR stage on its own)."""
    from voiceshop.speech import build_transcriber

    _logging()
    logging.getLogger().setLevel(logging.WARNING)
    transcriber = build_transcriber()
    if transcriber is None:
        console.print("[red]Speech recognition is off (ASR_PROVIDER=none).[/red]")
        raise typer.Exit(1)
    result = transcriber.transcribe(recording, word_timestamps=words)
    if as_json:
        console.print_json(json.dumps(result.to_dict()))
        return
    if result.status == "error":
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(1)
    for segment in result.segments:
        confidence = f"{segment.confidence:.2f}" if segment.confidence is not None else "-"
        console.print(f"[dim]\\[{segment.start:6.2f} - {segment.end:6.2f} s] confidence {confidence}[/dim]  {segment.text}", highlight=False)
        if words and segment.words:
            console.print("    [dim]" + "  ".join(f"{w.word}@{w.start:.2f}" for w in segment.words) + "[/dim]", highlight=False)
    confidence = f"{result.confidence:.2f}" if result.confidence is not None else "-"
    console.print(f"[bold]{result.text or '[nothing heard]'}[/bold]", highlight=False)
    console.print(f"[dim]status {result.status} | confidence {confidence} | {result.duration_s:.1f} s of audio ({result.speech_s:.1f} s speech) | "
                  f"{result.model} via {result.provider} | {result.seconds:.2f} s, real-time factor {result.real_time_factor}[/dim]", highlight=False)


@app.command()
def say(
    text: Annotated[str, typer.Argument(help="What to say")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the WAV file")] = Path("say.wav"),
    max_seconds: Annotated[float | None, typer.Option("--max-seconds", help="Length limit (default: TTS_MAX_SECONDS)")] = None,
) -> None:
    """Text to speech within the length limit (the TTS stage on its own)."""
    from voiceshop.config import get_settings
    from voiceshop.speech import build_synthesizer, speak_within

    _logging()
    logging.getLogger().setLevel(logging.WARNING)
    settings = get_settings()
    synthesizer = build_synthesizer(settings)
    if synthesizer is None:
        console.print("[red]Speech synthesis is off (TTS_PROVIDER=none).[/red]")
        raise typer.Exit(1)
    speech = speak_within(synthesizer, text, max_seconds=max_seconds or settings.tts_max_seconds, speed=settings.tts_speed)
    out.write_bytes(speech.audio)
    for sentence in speech.sentences:
        console.print(f"[dim]\\[{sentence['start_s']:5.2f} - {sentence['end_s']:5.2f} s][/dim]  {sentence['text']}", highlight=False)
    for sentence in speech.dropped_sentences:
        console.print(f"[yellow]left out to stay within the limit:[/yellow] {sentence}", highlight=False)
    console.print(f"[dim]{out} | {speech.duration_s:.2f} s at speed {speech.speed:g} (estimated {speech.estimated_s:.1f} s at normal speed) | "
                  f"{speech.voice} via {speech.provider} | first sentence ready after {speech.first_audio_s:.2f} s, all after {speech.seconds:.2f} s[/dim]",
                  highlight=False)


# ------------------------------------------------------------------------ mcp
mcp_app = typer.Typer(no_args_is_help=True, help="The MCP server (rag.search, web.search) and a client to inspect it")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[str | None, typer.Option(help="stdio | http (default: MCP_TRANSPORT)")] = None,
    host: Annotated[str | None, typer.Option(help="HTTP bind address (default: MCP_HTTP_HOST)")] = None,
    port: Annotated[int | None, typer.Option(help="HTTP port (default: MCP_HTTP_PORT)")] = None,
) -> None:
    """Start the MCP server and block. With stdio, stdout is the protocol channel (logs go to stderr)."""
    from voiceshop.mcp_server.server import run

    if transport not in (None, "stdio", "http"):
        raise typer.BadParameter("transport must be 'stdio' or 'http'")
    run(transport, host, port)  # type: ignore[arg-type]


def _with_client(coro_factory):  # type: ignore[no-untyped-def]
    """Open an MCP session the way the agents do (stdio subprocess, or MCP_SERVER_URL) and run one coroutine with it."""
    import asyncio

    from voiceshop.mcp_client import MCPConnectionError, MCPToolClient

    async def main():  # type: ignore[no-untyped-def]
        async with MCPToolClient() as client:
            return await coro_factory(client)

    try:
        return asyncio.run(main())
    except MCPConnectionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@mcp_app.command("tools")
def mcp_tools(
    schemas: Annotated[bool, typer.Option("--schemas", help="Also print each tool's input and output JSON Schema")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run MCP discovery (tools/list, resources/list) and show what the server advertises."""

    async def work(client):  # type: ignore[no-untyped-def]
        return client.describe(), client.tools

    described, tools = _with_client(work)
    if as_json:
        payload = {**described, "schemas": {t.name: {"input": t.input_schema, "output": t.output_schema} for t in tools}} if schemas else described
        console.print_json(json.dumps(payload, default=str))
        return
    console.print(f"server: [bold]{described['server'].get('name')}[/bold] {described['server'].get('version')}  "
                  f"protocol {described['server'].get('protocol')}  via {described['transport']}")
    table = Table("tool", "alias for the LLM", "arguments (required*)", "read-only", "open world")
    for t in described["tools"]:
        args = ", ".join(f"{a}*" if a in t["required"] else a for a in t["arguments"])
        table.add_row(t["name"], t["alias"], args, str(t["read_only"]), str(t["open_world"]))
    console.print(table)
    for r in described["resources"]:
        console.print(f"resource: {r['uri']}  [dim]{r['description']}[/dim]")
    for t in tools:
        console.print(f"\n[bold]{t.name}[/bold] - {t.title}\n{t.description}")
        if schemas:
            console.print("[dim]input schema[/dim]")
            console.print_json(json.dumps(t.input_schema))
            console.print("[dim]output schema[/dim]")
            console.print_json(json.dumps(t.output_schema))


@mcp_app.command("call")
def mcp_call(
    tool: Annotated[str, typer.Argument(help="rag.search or web.search")],
    arguments: Annotated[str, typer.Argument(help='JSON arguments, e.g. \'{"query": "wooden puzzle", "filters": {"price_max": 20}}\'')],
    full: Annotated[bool, typer.Option("--full", help="Print the whole structured result instead of the compact view")] = False,
) -> None:
    """Call one tool through MCP and print the result (the same path the agents use)."""
    from voiceshop.mcp_client.adapters import render_for_model

    try:
        args = json.loads(arguments)
    except ValueError as exc:
        raise typer.BadParameter(f"arguments must be JSON: {exc}") from exc

    async def work(client):  # type: ignore[no-untyped-def]
        return await client.call(tool, args)

    result = _with_client(work)
    if not result.ok:
        console.print(f"[red]error[/red] ({result.duration_ms:.0f} ms): {result.error}")
        raise typer.Exit(1)
    console.print_json(json.dumps(result.data) if full else render_for_model(result))
    console.print(f"[dim]{result.tool}: {len(result.results)} result(s) in {result.duration_ms:.0f} ms[/dim]")


# ----------------------------------------------------------------------- eval
eval_app = typer.Typer(no_args_is_help=True, help="Evaluation: component metrics and end-to-end scorecards")
app.add_typer(eval_app, name="eval")


@eval_app.command("retrieval")
def eval_retrieval(
    sample: Annotated[int | None, typer.Option(help="Score only this many queries (a seeded sample)")] = None,
    slow_sample: Annotated[int | None, typer.Option(help="Queries for the variant that re-ranks all 40 candidates (slow on a small CPU)")] = None,
    out: Annotated[Path | None, typer.Option(help="Output folder (default: eval/results/retrieval)")] = None,
) -> None:
    """Score every retrieval variant on the benchmark queries and write benchmark.json / benchmark.md."""
    from voiceshop.evals import retrieval_eval as ev
    from voiceshop.paths import project_root
    from voiceshop.retrieval.engine import SearchEngine

    _logging()
    root = project_root()
    engine = SearchEngine.load()
    try:
        if engine.reranker is None:
            console.print("[yellow]The re-ranker is not available (RERANKER_ENABLED or model files); its variants are skipped.[/yellow]")
        report = ev.run(engine, root / "eval" / "datasets" / "retrieval_queries.jsonl", out or (root / "eval" / "results" / "retrieval"),
                        max_queries=sample, rerank_max_queries=slow_sample)
    finally:
        engine.close()
    console.print(ev.to_markdown(report), highlight=False, markup=False)


if __name__ == "__main__":
    app()

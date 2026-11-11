"""Shared fixtures. The default test run needs no API key, network or model files."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "data" / "sample" / "raw_sample.csv"

# Tests must never spend money or read a developer's real configuration by accident.
os.environ.setdefault("LLM_MODEL", "fake:unit-test")
os.environ.setdefault("EMBEDDING_BACKEND", "hashing")
os.environ.setdefault("WEB_SEARCH_PROVIDER", "fixture")
os.environ.setdefault("RERANKER_ENABLED", "false")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-live", action="store_true", default=False, help="run tests that call a real LLM")
    parser.addoption("--run-models", action="store_true", default=False, help="run tests that need local model files")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_live = pytest.mark.skip(reason="needs --run-live (real LLM key and network)")
    skip_models = pytest.mark.skip(reason="needs --run-models (local model files)")
    for item in items:
        if "live" in item.keywords and not config.getoption("--run-live"):
            item.add_marker(skip_live)
        if "models" in item.keywords and not config.getoption("--run-models"):
            item.add_marker(skip_models)


@pytest.fixture(scope="session")
def raw_sample() -> pd.DataFrame:
    return pd.read_csv(SAMPLE_CSV, dtype=str)


@pytest.fixture(scope="session")
def products(raw_sample: pd.DataFrame) -> pd.DataFrame:
    from voiceshop.data.ingest import ingest_frame

    frame, _ = ingest_frame(raw_sample)
    return frame


# --------------------------------------------------------------------- agents
FIXTURE_WEB = ROOT / "data" / "sample" / "web_search_fixture.json"


@pytest.fixture(scope="session")
def sample_engine(products: pd.DataFrame):  # type: ignore[no-untyped-def]
    """A search engine over the committed sample (hashing embedder, no model files)."""
    from voiceshop.retrieval.embedder import HashingEmbedder
    from voiceshop.retrieval.engine import SearchEngine

    return SearchEngine.in_memory(products.to_dict(orient="records"), HashingEmbedder())


@pytest.fixture
def agent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Point the whole process at a throw-away configuration: rule-based agents, fixture web search, temp logs.

    Returns a function that sets further environment variables (for example ``LLM_MODEL=fake:<script>``).
    """
    from voiceshop.config import reload_settings
    from voiceshop.llm.factory import clear_model_cache
    from voiceshop.llm.ledger import reset_ledger

    def apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        reload_settings()
        clear_model_cache()
        reset_ledger()

    apply(LLM_MODEL="fake:rules", LOGS_DIR=str(tmp_path / "logs"), DATA_DIR=str(tmp_path / "data"), WEB_SEARCH_PROVIDER="fixture",
          WEB_SEARCH_FALLBACKS="", WEB_PRICE_CHECK="auto", EMBEDDING_BACKEND="hashing", RERANKER_ENABLED="false",
          **{f"LLM_MODEL_{role}": "" for role in ("ROUTER", "PLANNER", "RETRIEVER", "ANSWERER", "CRITIC")})
    yield apply
    monkeypatch.undo()
    reload_settings()
    clear_model_cache()
    reset_ledger()


@pytest.fixture
async def mcp_client(agent_env, sample_engine, tmp_path: Path):  # type: ignore[no-untyped-def]
    """An MCP client connected to an in-process server (sample catalog, fixture web results)."""
    from voiceshop.config import get_settings
    from voiceshop.mcp_client import MCPToolClient
    from voiceshop.mcp_server.controls import AuditLog
    from voiceshop.mcp_server.providers import FixtureProvider
    from voiceshop.mcp_server.server import build_server
    from voiceshop.mcp_server.web import WebSearchService

    settings = get_settings()
    server = build_server(settings, engine=sample_engine, web=WebSearchService([FixtureProvider(FIXTURE_WEB)], per_minute=600),
                          audit=AuditLog(tmp_path / "logs" / "mcp_calls.jsonl"))
    client = MCPToolClient(settings, server=server)
    await client.start()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def assistant(mcp_client):  # type: ignore[no-untyped-def]
    """The full assistant (text in, text out) on the in-process MCP server."""
    from voiceshop.assistant import Assistant

    bot = Assistant(mcp=mcp_client)             # follows the process configuration, so a test can change it mid-way
    await bot.start()
    try:
        yield bot
    finally:
        await bot.aclose()

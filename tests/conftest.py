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

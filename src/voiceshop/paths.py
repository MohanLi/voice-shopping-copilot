"""Where things live on disk.

Everything is resolved from one project root so the same code runs from a
checkout, a Docker image or an installed wheel:

1. ``VOICESHOP_HOME`` if set,
2. the repository root when the package is used from a checkout (editable install),
3. the current working directory.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_MARKER = "pyproject.toml"


@lru_cache(maxsize=1)
def project_root() -> Path:
    env = os.environ.get("VOICESHOP_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    # src/voiceshop/paths.py -> repo root is two levels above the package directory
    candidate = here.parents[2]
    if (candidate / _MARKER).exists() and (candidate / "src" / "voiceshop").exists():
        return candidate
    return Path.cwd().resolve()


def _bundled(name: str) -> Path:
    """Copies of prompts/ and configs/ shipped inside a wheel (see pyproject force-include)."""
    return Path(__file__).resolve().parent / "_bundled" / name


def prompts_dir() -> Path:
    p = project_root() / "prompts"
    return p if p.is_dir() else _bundled("prompts")


def configs_dir() -> Path:
    p = project_root() / "configs"
    return p if p.is_dir() else _bundled("configs")


def data_dir() -> Path:
    return project_root() / "data"


def models_dir() -> Path:
    return project_root() / "models"


def logs_dir() -> Path:
    p = project_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path

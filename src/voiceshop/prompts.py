"""Prompt loading.

Every prompt is a Markdown file in ``prompts/`` with a small YAML header:

    ---
    name: router
    version: 1
    used_by: LangGraph node `router` (voiceshop.agents.router)
    ---
    You are …  $placeholder …

``$placeholders`` are filled with :class:`string.Template` (so JSON braces in a
prompt need no escaping). The name, version and a content hash are attached to
every model call's trace, which is how "which prompt produced this answer" is
answered later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from string import Template
from typing import Any

import yaml

from voiceshop.paths import prompts_dir


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    used_by: str
    text: str
    sha: str

    def render(self, **values: Any) -> str:
        return Template(self.text).safe_substitute({k: str(v) for k, v in values.items()})

    @property
    def ref(self) -> dict[str, str]:
        """What gets logged with a model call."""
        return {"prompt": self.name, "version": self.version, "sha": self.sha}


@lru_cache(maxsize=64)
def load_prompt(name: str) -> Prompt:
    path = prompts_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        _, header, body = raw.split("---", 2)
        meta = yaml.safe_load(header) or {}
    text = body.strip()
    return Prompt(
        name=str(meta.get("name", name)),
        version=str(meta.get("version", "1")),
        used_by=str(meta.get("used_by", "")),
        text=text,
        sha=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    )


def list_prompts() -> list[Prompt]:
    return [load_prompt(p.stem) for p in sorted(prompts_dir().glob("*.md")) if p.name.lower() != "readme.md"]

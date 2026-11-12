"""Environment-driven configuration.

Every provider choice (LLM, web search, ASR, TTS, vector store) is a value in
``.env`` or the process environment, so swapping a model or a provider never
needs a code change. ``.env.example`` documents each variable.

``.env`` is also exported to ``os.environ`` (without overriding what is already
set) because the provider SDKs read their own keys from there.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voiceshop.paths import project_root

_ENV_FILE = project_root() / ".env"
load_dotenv(_ENV_FILE, override=False)

AgentRole = Literal["router", "planner", "retriever", "answerer", "critic"]
AGENT_ROLES: tuple[AgentRole, ...] = ("router", "planner", "retriever", "answerer", "critic")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ LLM
    # "<provider>:<model>", e.g. anthropic:claude-haiku-5-5, openai:gpt-5-mini,
    # ollama:llama3.1:8b, google_genai:gemini-2.5-flash
    llm_model: str = "anthropic:claude-haiku-5-5"
    # Optional per-agent overrides; empty means "use LLM_MODEL".
    llm_model_router: str = ""
    llm_model_planner: str = ""
    llm_model_retriever: str = ""
    llm_model_answerer: str = ""
    llm_model_critic: str = ""
    llm_max_tokens: int = 2000
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    # Hard stop for paid calls made by this process family (see observability/ledger.py).
    llm_budget_usd: float = 50.0

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    # ----------------------------------------------------------- web search
    web_search_provider: Literal["serper", "tavily", "brave", "anthropic", "fixture", "none"] = "serper"
    # Tried in order when the primary provider fails or is not configured.
    web_search_fallbacks: str = ""
    serper_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    web_search_timeout_s: float = 12.0
    web_search_country: str = "us"
    # Evaluation only: a file of planted faulty results (see mcp_server.providers.FaultProvider). Unset in normal use.
    web_search_faults: Path | None = None
    # Comma-separated host suffixes. Empty allow-list = any host not on the block-list.
    web_allowed_domains: str = ""
    web_blocked_domains: str = "pinterest.com,facebook.com,instagram.com,tiktok.com,reddit.com,quora.com"

    # ------------------------------------------------------------------ MCP
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8765
    mcp_server_url: str = ""  # connect to an already running streamable-HTTP server instead of spawning one
    mcp_cache_ttl_s: int = Field(default=180, ge=0, le=3600)
    mcp_rate_web_per_min: int = Field(default=20, ge=1)
    mcp_rate_rag_per_min: int = Field(default=240, ge=1)

    # ------------------------------------------------------------ retrieval
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_backend: Literal["fastembed", "hashing"] = "fastembed"
    # Product text is truncated to this many tokens before embedding (title and features come first).
    embedding_max_tokens: int = Field(default=256, ge=32, le=512)
    # Text put in front of a query before it is embedded. Unset: the instruction the model's authors recommend for
    # retrieval, if there is one (bge: "Represent this sentence for searching relevant passages: "). Empty: none.
    embedding_query_instruction: str | None = None
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    reranker_enabled: bool = True
    qdrant_url: str = ""  # empty = embedded local mode under data/index/qdrant
    qdrant_collection: str = "products"
    retrieval_top_k: int = 5
    retrieval_candidates: int = 40
    rrf_k: int = 60
    # The cross-encoder scores this many of the fused candidates and votes with this weight (see docs/retrieval.md).
    rerank_top_n: int = Field(default=10, ge=1, le=50)
    rerank_weight: float = Field(default=1.0, ge=0.0, le=5.0)

    # --------------------------------------------------------------- speech
    asr_provider: Literal["faster_whisper", "openai", "none"] = "faster_whisper"
    asr_model: str = "small.en"
    asr_device: str = "auto"
    asr_compute_type: str = "int8"
    asr_max_seconds: float = Field(default=30.0, ge=3.0, le=120.0)     # longer recordings: only the start is transcribed
    asr_low_confidence: float = Field(default=0.45, ge=0.0, le=1.0)    # below: used, but shown as uncertain
    asr_reject_confidence: float = Field(default=0.25, ge=0.0, le=1.0) # below: the shopper is asked to repeat
    tts_provider: Literal["kokoro", "openai", "elevenlabs", "none"] = "kokoro"
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    tts_max_seconds: float = 15.0
    elevenlabs_api_key: SecretStr | None = None

    # --------------------------------------------------------------- agents
    # auto: the planner decides (web when the shopper asks for current/latest info or the catalog cannot answer);
    # always: also check the live price of the top catalog matches on every product request; never: catalog only.
    web_price_check: Literal["auto", "always", "never"] = "auto"
    # Tool-calling rounds the Retriever may add after the planned searches.
    retriever_max_rounds: int = Field(default=2, ge=0, le=4)
    retriever_max_calls_per_round: int = Field(default=4, ge=1, le=6)
    # How many times the Answerer may revise after the Critic objects before the grounded template is used.
    critic_max_revisions: int = Field(default=1, ge=0, le=2)
    critic_llm_enabled: bool = True
    # Re-ranker score below which the best catalog match is treated as "the catalog has nothing relevant".
    weak_match_score: float = -4.0
    # Spoken summary budget in words (lead + "N more options ..." + question); the audio is also held to TTS_MAX_SECONDS.
    spoken_max_words: int = Field(default=45, ge=20, le=80)
    memory_turns: int = Field(default=4, ge=0, le=12)
    # Write full prompts and model outputs to logs/llm_calls.jsonl (prompt name, version and hash are always logged).
    log_prompts: bool = True

    # ------------------------------------------------------------- service
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    # Where relative data/model/log paths resolve from; defaults to the repo root.
    data_dir: Path = Field(default_factory=lambda: project_root() / "data")
    models_dir: Path = Field(default_factory=lambda: project_root() / "models")
    logs_dir: Path = Field(default_factory=lambda: project_root() / "logs")

    # -------------------------------------------------------------- helpers
    @field_validator("llm_model")
    @classmethod
    def _has_provider(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError(
                f"LLM_MODEL must be '<provider>:<model>' (e.g. anthropic:claude-haiku-5-5), got {v!r}"
            )
        return v

    def model_for(self, role: AgentRole) -> str:
        """Model spec for one agent role, falling back to LLM_MODEL."""
        override = getattr(self, f"llm_model_{role}", "") or ""
        return override.strip() or self.llm_model

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [x.strip().lower() for x in value.split(",") if x.strip()]

    @property
    def allowed_domains(self) -> list[str]:
        return self._csv(self.web_allowed_domains)

    @property
    def blocked_domains(self) -> list[str]:
        return self._csv(self.web_blocked_domains)

    @property
    def search_provider_chain(self) -> list[str]:
        chain = [self.web_search_provider, *self._csv(self.web_search_fallbacks)]
        seen: list[str] = []
        for p in chain:
            if p and p != "none" and p not in seen:
                seen.append(p)
        return seen

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "index" / "qdrant"

    def public_dict(self) -> dict[str, object]:
        """Effective configuration with every secret masked (safe to log or show in the UI)."""
        out: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if isinstance(value, SecretStr) or name.endswith("_api_key"):
                out[name] = "set" if value else "missing"
            elif isinstance(value, Path):
                out[name] = str(value)
            else:
                out[name] = value
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings (tests change the environment between cases)."""
    get_settings.cache_clear()
    return get_settings()

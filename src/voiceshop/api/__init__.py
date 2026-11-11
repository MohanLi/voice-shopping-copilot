"""The HTTP API behind the web UI (FastAPI, server-sent events)."""

from voiceshop.api.app import create_app

__all__ = ["create_app"]

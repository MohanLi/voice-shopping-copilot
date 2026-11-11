"""Run coroutines from synchronous code, whether or not an event loop is already running.

The agents and the MCP client are async. Synchronous callers (the CLI, notebooks, the
ProofAgent harness, which calls the agent as a plain ``str -> str`` function) use one
long-lived event loop in a background thread, so an MCP session opened once keeps
working across calls and nothing breaks when the caller itself runs inside a loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class BackgroundLoop:
    def __init__(self, name: str = "voiceshop-loop") -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Run ``coro`` on the background loop and wait for its result."""
        if threading.current_thread() is self._thread:
            raise RuntimeError("BackgroundLoop.run() was called from its own loop thread; await the coroutine instead")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()

    def __enter__(self) -> BackgroundLoop:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

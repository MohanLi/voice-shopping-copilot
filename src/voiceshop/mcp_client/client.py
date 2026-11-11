"""MCP client used by the agents.

Nothing about the tools is hard-coded here: the client connects, runs MCP
*discovery* (``tools/list``) and builds its tool table from what the server
advertises — names, descriptions, input and output JSON Schemas. The agents'
function-calling definitions are generated from that table (``adapters.py``).

Three ways to reach the server, chosen by configuration:

* **stdio** (default) — spawn ``python -m voiceshop.mcp_server`` as a subprocess;
* **streamable HTTP** — connect to ``MCP_SERVER_URL`` (e.g. ``http://127.0.0.1:8765/mcp``);
* **in-process** — an already built server object (tests).

The MCP session lives in one background task, so it can be opened in an
application's start-up hook and used from any request handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, McpError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from voiceshop import __version__
from voiceshop.config import Settings, get_settings
from voiceshop.paths import project_root

log = logging.getLogger(__name__)

# Tool restriction: the agents may only ever call these tools, whatever a server advertises.
ALLOWED_TOOLS: tuple[str, ...] = ("rag.search", "web.search")


class MCPConnectionError(RuntimeError):
    """The MCP server could not be started or reached."""


class UnknownToolError(KeyError):
    """The requested tool is not one the server advertised (or not one the agents are allowed to call)."""


def alias_for(name: str) -> str:
    """Function-calling names cannot contain dots: ``rag.search`` is offered to the model as ``rag_search``."""
    return name.replace(".", "_").replace("/", "_")


@dataclass(frozen=True)
class ToolSpec:
    """One discovered tool."""

    name: str                      # MCP name, e.g. "rag.search"
    alias: str                     # function-calling name, e.g. "rag_search"
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name, "alias": self.alias, "title": self.title,
            "arguments": list((self.input_schema.get("properties") or {}).keys()),
            "required": self.input_schema.get("required") or [],
            "read_only": self.annotations.get("readOnlyHint"), "open_world": self.annotations.get("openWorldHint"),
        }


@dataclass
class ToolCallResult:
    """Outcome of one tool call, kept in the trace and shown in the UI's step log."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    data: dict[str, Any] | None = None       # structuredContent, validated by the server against the output schema
    error: str | None = None
    duration_ms: float = 0.0
    started_at: float = 0.0

    @property
    def results(self) -> list[dict[str, Any]]:
        return list((self.data or {}).get("results") or [])

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tool": self.tool, "arguments": self.arguments, "ok": self.ok, "duration_ms": round(self.duration_ms, 1)}
        if self.ok:
            out["n_results"] = len(self.results)
            for key in ("provider", "cached", "relaxed", "strict_matches"):
                if key in (self.data or {}):
                    out[key] = (self.data or {})[key]
        else:
            out["error"] = self.error
        return out


def tidy_error(text: str) -> str:
    """Drop pydantic's documentation links and collapse whitespace so the model gets a short, usable message."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip() and "errors.pydantic.dev" not in ln]
    return " ".join(lines)[:600] or "the tool failed without a message"


class MCPToolClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        server: Any = None,
        allowed_tools: Iterable[str] = ALLOWED_TOOLS,
        call_timeout_s: float = 90.0,
    ) -> None:
        self.settings = settings or get_settings()
        self._server = server
        self._allowed = tuple(allowed_tools)
        self.call_timeout_s = call_timeout_s
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._specs: dict[str, ToolSpec] = {}
        self.ignored_tools: list[str] = []
        self.resources: list[dict[str, str]] = []
        self.server_info: dict[str, Any] = {}
        self.instructions: str = ""

    # --------------------------------------------------------------- lifecycle
    @property
    def transport(self) -> str:
        if self._server is not None:
            return "in-process"
        if self.settings.mcp_server_url:
            return f"streamable-http {self.settings.mcp_server_url}"
        return "stdio (python -m voiceshop.mcp_server)"

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def start(self) -> MCPToolClient:
        if self._task is not None:
            return self
        self._ready, self._stop, self._error = asyncio.Event(), asyncio.Event(), None
        self._task = asyncio.create_task(self._serve(), name="mcp-session")
        await self._ready.wait()
        if self._error is not None:
            task, self._task = self._task, None
            await asyncio.gather(task, return_exceptions=True)
            raise MCPConnectionError(f"Could not connect to the MCP server over {self.transport}: {self._describe(self._error)}") from self._error
        return self

    async def aclose(self) -> None:
        if self._task is None or self._stop is None:
            return
        self._stop.set()
        task, self._task = self._task, None
        await asyncio.gather(task, return_exceptions=True)

    async def __aenter__(self) -> MCPToolClient:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _serve(self) -> None:
        """Own the session for its whole life: MCP transports must be opened and closed by the same task."""
        assert self._ready is not None and self._stop is not None
        try:
            async with self._connect() as session:
                self._session = session
                await self._discover(session)
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:
            if not self._ready.is_set():
                self._error = exc
                self._ready.set()
            elif not isinstance(exc, asyncio.CancelledError):
                log.warning("MCP session ended unexpectedly: %s", self._describe(exc))
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._session = None

    @staticmethod
    def _describe(exc: BaseException) -> str:
        inner = getattr(exc, "exceptions", None)          # anyio wraps failures in an ExceptionGroup
        if inner:
            return "; ".join(MCPToolClient._describe(e) for e in inner)
        return f"{type(exc).__name__}: {exc}"

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[ClientSession]:
        info = Implementation(name="voiceshop-agents", version=__version__)
        if self._server is not None:
            from mcp.shared.memory import create_connected_server_and_client_session

            async with create_connected_server_and_client_session(self._server, client_info=info) as session:
                self._remember(await session.initialize())
                yield session
        elif self.settings.mcp_server_url:
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(self.settings.mcp_server_url) as (read, write, _), ClientSession(read, write, client_info=info) as session:
                self._remember(await session.initialize())
                yield session
        else:
            self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "voiceshop.mcp_server", "--transport", "stdio"],
                env={**os.environ, "PYTHONUNBUFFERED": "1"},     # the server needs the same configuration and keys
                cwd=str(project_root()),
            )
            with (self.settings.logs_dir / "mcp_server.log").open("a", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write, client_info=info) as session:
                    self._remember(await session.initialize())
                    yield session

    def _remember(self, init: Any) -> None:
        self.server_info = {"name": init.serverInfo.name, "version": init.serverInfo.version, "protocol": init.protocolVersion}
        self.instructions = init.instructions or ""

    # --------------------------------------------------------------- discovery
    async def _discover(self, session: ClientSession) -> None:
        listed = (await session.list_tools()).tools
        specs: dict[str, ToolSpec] = {}
        ignored: list[str] = []
        for tool in listed:
            if tool.name not in self._allowed:
                ignored.append(tool.name)
                continue
            specs[tool.name] = ToolSpec(
                name=tool.name,
                alias=alias_for(tool.name),
                title=tool.title or tool.name,
                description=(tool.description or "").strip(),
                input_schema=tool.inputSchema,
                output_schema=tool.outputSchema,
                annotations=tool.annotations.model_dump(exclude_none=True) if tool.annotations else {},
            )
        if ignored:
            log.warning("ignoring tools outside the allow-list: %s", ", ".join(ignored))
        self._specs, self.ignored_tools = specs, ignored
        try:
            listed_resources = (await session.list_resources()).resources
        except McpError:                                    # resources are optional in MCP
            listed_resources = []
        self.resources = [{"uri": str(r.uri), "name": r.name, "description": r.description or ""} for r in listed_resources]

    @property
    def tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def spec(self, name: str) -> ToolSpec:
        """Look a tool up by its MCP name or its function-calling alias."""
        if name in self._specs:
            return self._specs[name]
        for spec in self._specs.values():
            if spec.alias == name:
                return spec
        raise UnknownToolError(f"{name!r} is not an available tool; discovered: {', '.join(self._specs) or 'none'}")

    # ------------------------------------------------------------------- calls
    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPConnectionError("The MCP client is not connected; call start() first")
        return self._session

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolCallResult:
        """Call a tool. Failures come back as ``ok=False`` with a message the calling model can act on."""
        args = dict(arguments or {})
        started_at, started = time.time(), time.perf_counter()

        def done(**kw: Any) -> ToolCallResult:
            return ToolCallResult(tool=tool_name, arguments=args, duration_ms=(time.perf_counter() - started) * 1000, started_at=started_at, **kw)

        tool_name = name
        try:
            tool_name = self.spec(name).name
        except UnknownToolError as exc:
            return done(ok=False, error=str(exc.args[0]))
        try:
            result = await asyncio.wait_for(self._require_session().call_tool(tool_name, args), timeout=self.call_timeout_s)
        except TimeoutError:
            return done(ok=False, error=f"{tool_name} timed out after {self.call_timeout_s:.0f} s")
        except (McpError, MCPConnectionError) as exc:
            return done(ok=False, error=tidy_error(str(exc)))
        text = "\n".join(block.text for block in result.content if getattr(block, "text", None))
        if result.isError:
            return done(ok=False, error=tidy_error(text))
        data = result.structuredContent
        if data is None:
            try:
                data = json.loads(text)
            except ValueError:
                return done(ok=False, error=f"{tool_name} returned no structured content")
        return done(ok=True, data=data)

    async def read_resource(self, uri: str) -> str:
        from pydantic import AnyUrl

        result = await self._require_session().read_resource(AnyUrl(uri))
        return "\n".join(c.text for c in result.contents if getattr(c, "text", None))

    def describe(self) -> dict[str, Any]:
        """What discovery found — shown by ``voiceshop mcp tools`` and in the UI."""
        return {
            "transport": self.transport,
            "server": self.server_info,
            "tools": [spec.summary() for spec in self.tools],
            "ignored_tools": self.ignored_tools,
            "resources": self.resources,
        }

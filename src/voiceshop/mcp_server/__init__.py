"""MCP server exposing the project's two tools, ``rag.search`` and ``web.search``.

Import :func:`build_server` to embed or test the server; run ``python -m voiceshop.mcp_server`` to start it.
"""

from voiceshop.mcp_server.schemas import RAG_TOOL, TOOL_NAMES, WEB_TOOL

__all__ = ["RAG_TOOL", "TOOL_NAMES", "WEB_TOOL", "build_server"]


def __getattr__(name: str):  # lazy: importing the package must not import the MCP SDK or retrieval code
    if name == "build_server":
        from voiceshop.mcp_server.server import build_server

        return build_server
    raise AttributeError(name)

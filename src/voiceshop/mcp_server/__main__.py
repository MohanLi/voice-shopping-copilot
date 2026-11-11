"""``python -m voiceshop.mcp_server`` — start the MCP server (stdio by default)."""

from __future__ import annotations

import argparse

from voiceshop.mcp_server.server import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="voiceshop.mcp_server", description="MCP server exposing rag.search and web.search")
    parser.add_argument("--transport", choices=["stdio", "http"], default=None, help="default: MCP_TRANSPORT (stdio)")
    parser.add_argument("--host", default=None, help="HTTP bind address (default: MCP_HTTP_HOST)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default: MCP_HTTP_PORT)")
    args = parser.parse_args()
    run(args.transport, args.host, args.port)


if __name__ == "__main__":
    main()

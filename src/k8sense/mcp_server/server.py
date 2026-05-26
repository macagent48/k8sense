"""Build and run the k8sense MCP server."""

from __future__ import annotations

from typing import Any

from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from k8sense.mcp_server.prompts import register_prompts
from k8sense.mcp_server.resources import register_resources
from k8sense.tools.registry import all_tool_specs


def build_server() -> Server:
    """Construct a configured MCP Server with all k8sense tools, resources, and prompts."""
    server = Server("k8sense", version="0.3.0")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_model.model_json_schema(),
            )
            for spec in all_tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        for spec in all_tool_specs():
            if spec.name == name:
                validated = spec.input_model(**arguments).model_dump(exclude_none=True)
                result = await spec.handler(validated)
                # The handler returns {"content": [{"type": "text", "text": ...}]}.
                # Convert each block to a TextContent for the MCP layer.
                return [
                    TextContent(type="text", text=block["text"])
                    for block in result["content"]
                    if block.get("type") == "text"
                ]
        raise ValueError(f"unknown tool: {name}")

    register_resources(server)
    register_prompts(server)
    return server


async def run_stdio() -> None:
    """Run the MCP server over stdio. Returns when the parent closes the pipe."""
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )

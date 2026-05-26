"""End-to-end smoke: spawn `k8sense mcp` as a subprocess, complete the MCP
initialization handshake, then send tools/list, resources/list, and prompts/list
and assert the responses contain the expected names.

Run with:
    K8SENSE_ALLOW_API=1 pytest -m smoke -s tests/smoke/test_mcp_stdio.py
"""

import asyncio
import json
import os

import pytest


async def _request(
    proc: asyncio.subprocess.Process, body: dict, request_id: int
) -> dict:
    """Send a JSON-RPC request over stdin, read one line of response from stdout."""
    payload = {"jsonrpc": "2.0", "id": request_id, **body}
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    return json.loads(line.decode())


async def _notify(proc: asyncio.subprocess.Process, body: dict) -> None:
    """Send a JSON-RPC notification (no response expected)."""
    payload = {"jsonrpc": "2.0", **body}
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()


async def _run_mcp_session() -> dict:
    proc = await asyncio.create_subprocess_exec(
        ".venv/bin/k8sense",
        "mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # 1. initialize
        init_resp = await _request(
            proc,
            {
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "k8sense-smoke", "version": "0"},
                },
            },
            1,
        )
        # 2. notifications/initialized — required handshake completion
        await _notify(proc, {"method": "notifications/initialized"})

        # 3. tools/list
        tools_resp = await _request(proc, {"method": "tools/list"}, 2)
        # 4. resources/list
        resources_resp = await _request(proc, {"method": "resources/list"}, 3)
        # 5. prompts/list
        prompts_resp = await _request(proc, {"method": "prompts/list"}, 4)

        # 6. tools/call — exercise the call path with a disallowed verb so no cluster work runs
        call_resp = await _request(
            proc,
            {
                "method": "tools/call",
                "params": {
                    "name": "kubectl",
                    "arguments": {"args": ["delete", "pod", "x"]},
                },
            },
            5,
        )
        # 7. resources/read — exercise the read path for topology
        read_resp = await _request(
            proc,
            {
                "method": "resources/read",
                "params": {"uri": "mcp://k8sense/topology"},
            },
            6,
        )

        return {
            "init": init_resp,
            "tools": tools_resp,
            "resources": resources_resp,
            "prompts": prompts_resp,
            "call": call_resp,
            "read": read_resp,
        }
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.terminate()
            await proc.wait()


@pytest.mark.smoke
def test_mcp_stdio_handshake_and_lists():
    if not os.environ.get("K8SENSE_ALLOW_API"):
        pytest.skip("smoke test requires K8SENSE_ALLOW_API=1")

    result = asyncio.run(_run_mcp_session())

    # Initialize must succeed
    assert "result" in result["init"], f"init failed: {result['init']}"
    assert result["init"]["result"]["serverInfo"]["name"] == "k8sense"
    assert result["init"]["result"]["serverInfo"]["version"] == "0.3.0"

    # tools/list returns kubectl and prometheus_query
    tool_names = {t["name"] for t in result["tools"]["result"]["tools"]}
    assert tool_names == {"kubectl", "prometheus_query"}, (
        f"unexpected tools: {tool_names}"
    )

    # resources/list returns topology and events/recent (the templated one is in resources/templates/list)
    resource_uris = {str(r["uri"]) for r in result["resources"]["result"]["resources"]}
    assert "mcp://k8sense/topology" in resource_uris
    assert "mcp://k8sense/events/recent" in resource_uris

    # prompts/list returns the three workflow prompts
    prompt_names = {p["name"] for p in result["prompts"]["result"]["prompts"]}
    assert prompt_names == {"investigate-pod", "triage-events", "metrics"}

    # tools/call routed to the kubectl handler; the allowlist rejection text appears.
    # The MCP layer reports tool errors via isError=True with the rejection in the content.
    call_content = result["call"]["result"]["content"]
    call_text = " ".join(block.get("text", "") for block in call_content)
    assert "not allowed" in call_text or "delete" in call_text, (
        f"unexpected call response: {result['call']}"
    )

    # resources/read for topology returned at least one content block.
    read_contents = result["read"]["result"]["contents"]
    assert len(read_contents) >= 1
    # The first content's text mentions either the markdown heading or stderr (if kubectl is hidden in CI)
    first_text = read_contents[0].get("text", "")
    assert (
        "topology" in first_text.lower()
        or "topology fetch failed" in first_text.lower()
    )

"""Tool registry — shared by the in-process SDK transport and the stdio MCP transport.

Defines:
- Pydantic input models (KubectlInput, PrometheusInput)
- ToolSpec dataclass (transport-agnostic tool description)
- all_tool_specs() factory
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field


class KubectlInput(BaseModel):
    args: list[str] = Field(
        min_length=1,
        description=(
            "kubectl argv. Allowed first verb: get|describe|logs|top|events|version. "
            'Example: ["get", "pods", "-n", "argocd"].'
        ),
    )


class PrometheusInput(BaseModel):
    query: str = Field(min_length=1, description="PromQL expression")
    lookback: str | None = Field(
        default=None,
        pattern=r"^\d+[smhd]$",
        description="Range window (e.g. '5m', '1h', '24h'). Omit for instant query.",
    )


@dataclass
class ToolSpec:
    """Transport-agnostic description of one k8sense tool."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

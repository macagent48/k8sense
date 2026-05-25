"""Subagent definitions for k8sense Phase 2."""

from k8sense.subagents.event_triager import DEFINITION as event_triager_definition
from k8sense.subagents.log_investigator import DEFINITION as log_investigator_definition

__all__ = [
    "event_triager_definition",
    "log_investigator_definition",
]

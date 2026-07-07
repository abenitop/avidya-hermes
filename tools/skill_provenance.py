"""Skill write-origin provenance — ContextVar for distinguishing agent-sediment skill writes from foreground user-directed writes.

The curator only consolidates/prunes skills it autonomously created via the
background self-improvement review fork. Skills a user asks a foreground
agent to write belong to the user and must never be auto-curated.

This module exposes a ContextVar that run_agent.py sets before each tool
loop so tool handlers (e.g. skill_manage create) can check whether they
are executing inside the background-review fork.

The signal piggybacks on AIAgent._memory_write_origin, which is already
set to "background_review" for review-fork instances (see
_spawn_background_review in run_agent.py) and defaults to "assistant_tool"
for normal (foreground) agents.

Usage:
    from tools.skill_provenance import (
        set_current_write_origin,
        reset_current_write_origin,
        get_current_write_origin,
    )

    token = set_current_write_origin("background_review")
    try:
        ...  # tool runs here
    finally:
        reset_current_write_origin(token)

    # inside a tool:
    if get_current_write_origin() == "background_review":
        mark_agent_created(skill_name)
"""

import contextvars


_write_origin: contextvars.ContextVar[str] = contextvars.ContextVar(
    "skill_write_origin",
    default="foreground",
)

# The sentinel value the background review fork uses; mirrors
# run_agent.py's AIAgent._memory_write_origin override in
# _spawn_background_review().
BACKGROUND_REVIEW = "background_review"


def set_current_write_origin(origin: str) -> contextvars.Token[str]:
    """Bind the active write origin to the current context.

    Returns a Token the caller must pass to reset_current_write_origin
    in a finally block.
    """
    return _write_origin.set(origin or "foreground")


def reset_current_write_origin(token: contextvars.Token[str]) -> None:
    """Restore the prior write origin context."""
    _write_origin.reset(token)


def get_current_write_origin() -> str:
    """Return the active write origin.

    Default: "foreground" — any tool call made by a regular (non-review)
    agent, from the CLI, the gateway, cron, or a subagent.

    "background_review" — the self-improvement review fork; only skills
    created under this origin should be marked agent-created for curator
    management.
    """
    return _write_origin.get()


def is_background_review() -> bool:
    """Convenience: True iff the current write origin is the background
    review fork."""
    return get_current_write_origin() == BACKGROUND_REVIEW


_write_platform: contextvars.ContextVar[str] = contextvars.ContextVar(
    "skill_write_platform",
    default="",
)

# The tag agent/curator.py's forked review_agent carries
# (`AIAgent(platform="curator", ...)`), distinct from the live per-turn
# background_review fork, which inherits its parent conversation's platform
# (whatsapp/discord/telegram/cli/gateway/...) and is never "curator". Added
# 2026-07-08 to let skill_manager_tool.py's incident guard distinguish the
# two: both share write origin BACKGROUND_REVIEW (see module docstring), but
# only the live fork was implicated in the 2026-07-07 unattended-write
# incident — the scheduled curator has its own, older, incident-informed
# safety net (_curator_consolidation_delete_guard, #29912) and should not be
# swept into a block meant for the other mechanism.
CURATOR_PLATFORM = "curator"


def set_current_write_platform(platform: str) -> contextvars.Token[str]:
    """Bind the active turn's agent platform to the current context.

    Returns a Token the caller must pass to reset_current_write_platform
    in a finally block.
    """
    return _write_platform.set(platform or "")


def reset_current_write_platform(token: contextvars.Token[str]) -> None:
    """Restore the prior write platform context."""
    _write_platform.reset(token)


def get_current_write_platform() -> str:
    """Return the active turn's agent platform (empty string if unset)."""
    return _write_platform.get()


def is_curator_platform() -> bool:
    """Convenience: True iff the current turn's agent platform is the
    scheduled curator job (agent/curator.py), not the live per-turn
    background_review fork or any foreground platform."""
    return get_current_write_platform() == CURATOR_PLATFORM

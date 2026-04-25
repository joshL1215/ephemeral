from ephemeral.docker.catalog import ImageProfile
from .context import ContextWindow


async def build_system_prompt(
    catalog: list[ImageProfile],
    pool_stats: dict[str, int],
    context_window: ContextWindow,
) -> str:
    profiles_block = "\n".join(
        f"- {p.name}: {p.description} "
        f"(use cases: {', '.join(p.typical_use_cases)})"
        for p in catalog
    )

    pool_block = (
        "\n".join(f"  {k}: {v}" for k, v in sorted(pool_stats.items()))
        if pool_stats
        else "  (empty)"
    )

    context_block = context_window.render_for_prompt() or "(no events yet)"

    return f"""You are EPHEMERAL, a predictive provisioning agent for AI agent sandboxes.
Your job: look at recent context from a developer's AI agent and decide which Docker container profiles to pre-warm so they are ready before needed.

## Available profiles
{profiles_block}

## Current pool state
{pool_block}

## Recent agent context
{context_block}

## Instructions
- Call warm_containers if you predict the agent will need a profile soon and the pool lacks ready containers for it.
- Call no_action if the pool already has slack for the predicted need, or if context is too ambiguous to act.
- Never warm more than 5 containers total across all calls in one decision.
- Prefer warming early and slightly over-provisioning to under-provisioning.
- Base your decision on concrete signals in the context (file types, tool names, user intent). Don't warm speculatively on noise.
- Always include your reasoning."""

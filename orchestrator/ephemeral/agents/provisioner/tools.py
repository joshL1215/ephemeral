import logging

from ephemeral.docker.catalog import CATALOG
from ephemeral.docker.models import ContainerSpec, ResourceTier
from ephemeral.docker.service import ContainerService

_log = logging.getLogger("ephemeral.provisioner.tools")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "warm_containers",
            "description": (
                "Pre-warm one or more containers of the given profile and resource tier. "
                "Call this when you predict the developer's agent will soon need this environment. "
                "Choose the resource tier based on workload intensity: "
                "light for simple scripts/API calls, medium for general data work, "
                "heavy for ML training, large dataset processing, or compute-intensive tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_name": {
                        "type": "string",
                        "enum": [p.name for p in CATALOG],
                    },
                    "resource_tier": {
                        "type": "string",
                        "enum": ["light", "medium", "heavy"],
                        "description": (
                            "light: 0.5 CPU / 256MB — simple scripts, string ops, small API calls. "
                            "medium: 1 CPU / 512MB — pandas on moderate datasets, general data work. "
                            "heavy: 2 CPU / 2GB — scikit-learn training, large CSVs, numerical simulations."
                        ),
                    },
                    "count": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
                    "reasoning": {"type": "string"},
                },
                "required": ["profile_name", "resource_tier", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_action",
            "description": (
                "Indicate that no provisioning action is needed. Use when the "
                "current pool already covers predicted needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {"reasoning": {"type": "string"}},
                "required": ["reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_containers",
            "description": (
                "Kill one or more idle containers to free server resources. "
                "Only kill containers in 'ready' state. Never kill 'assigned' containers. "
                "Prefer killing older containers and containers not predicted for an active session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "container_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of container IDs to kill.",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["container_ids", "reasoning"],
            },
        },
    },
]


async def dispatch_tool_call(
    tool_name: str,
    tool_args: dict,
    container_service: ContainerService,
    session_id: str | None = None,
) -> dict:
    if tool_name == "warm_containers":
        profile_name = tool_args["profile_name"]
        tier = tool_args.get("resource_tier", "medium")
        count = int(tool_args.get("count", 1))
        reasoning = tool_args.get("reasoning", "")

        try:
            resource_tier = ResourceTier(tier)
        except ValueError:
            resource_tier = ResourceTier.medium
        spec = ContainerSpec(profile_name=profile_name, resource_tier=resource_tier, predicted_for=session_id)

        _log.info("Warming %d x %s [%s] — %s", count, profile_name, tier, reasoning)
        containers = await container_service.warm(profile_name, count=count, spec=spec)
        return {
            "action": "warmed",
            "profile": profile_name,
            "resource_tier": resource_tier.value,
            "memory_mb": spec.memory_mb,
            "cpu_quota": spec.cpu_quota,
            "count": len(containers),
            "container_ids": [c.id for c in containers],
            "reasoning": reasoning,
        }

    if tool_name == "no_action":
        reasoning = tool_args.get("reasoning", "")
        _log.info("No action — %s", reasoning)
        return {"action": "no_action", "reasoning": reasoning}

    if tool_name == "kill_containers":
        container_ids = tool_args.get("container_ids", [])
        reasoning = tool_args.get("reasoning", "")
        killed = []
        for cid in container_ids:
            await container_service.kill(cid, reason=f"pruning: {reasoning}")
            killed.append(cid)
        _log.info("Pruned %d containers — %s", len(killed), reasoning)
        return {"action": "killed", "container_ids": killed, "reasoning": reasoning}

    _log.warning("Unknown tool call: %s", tool_name)
    return {"action": "unknown", "tool": tool_name}

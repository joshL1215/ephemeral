import logging

from ephemeral.docker.catalog import CATALOG
from ephemeral.docker.service import ContainerService

_log = logging.getLogger("ephemeral.provisioner.tools")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "warm_containers",
            "description": (
                "Pre-warm one or more containers of the given profile. Call this when "
                "you predict the developer's agent will soon need this kind of environment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_name": {
                        "type": "string",
                        "enum": [p.name for p in CATALOG],
                    },
                    "count": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
                    "reasoning": {"type": "string"},
                },
                "required": ["profile_name", "reasoning"],
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
]


async def dispatch_tool_call(
    tool_name: str,
    tool_args: dict,
    container_service: ContainerService,
) -> dict:
    if tool_name == "warm_containers":
        profile_name = tool_args["profile_name"]
        count = int(tool_args.get("count", 1))
        reasoning = tool_args.get("reasoning", "")
        _log.info("Warming %d x %s — %s", count, profile_name, reasoning)
        containers = await container_service.warm(profile_name, count=count)
        return {
            "action": "warmed",
            "profile": profile_name,
            "count": len(containers),
            "container_ids": [c.id for c in containers],
            "reasoning": reasoning,
        }

    if tool_name == "no_action":
        reasoning = tool_args.get("reasoning", "")
        _log.info("No action — %s", reasoning)
        return {"action": "no_action", "reasoning": reasoning}

    _log.warning("Unknown tool call: %s", tool_name)
    return {"action": "unknown", "tool": tool_name}

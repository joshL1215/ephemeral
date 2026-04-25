import logging

from ephemeral.docker.catalog import CATALOG
from ephemeral.docker.service import ContainerService
from ephemeral.docker.events import publish

from .context import ContextEvent, ContextWindow
from .k2_client import K2Client, ToolCall
from .prompt import build_system_prompt
from .tools import TOOLS, dispatch_tool_call

_log = logging.getLogger("ephemeral.provisioner")

_MAX_POOL_PER_SESSION = 5


class ProvisionerAgent:
    def __init__(
        self,
        container_service: ContainerService,
        k2_client: K2Client,
    ) -> None:
        self._svc = container_service
        self._k2 = k2_client
        self._windows: dict[str, ContextWindow] = {}
        self._session_warm_counts: dict[str, int] = {}

    async def on_context_event(self, session_id: str, event: ContextEvent) -> None:
        window = self._windows.setdefault(session_id, ContextWindow())
        window.add(event)

        tool_calls = await self._llm_predict(session_id, window)
        for tc in tool_calls:
            if tc.name == "warm_containers":
                profile = tc.arguments.get("profile_name", "")
                count = int(tc.arguments.get("count", 1))
                reasoning = tc.arguments.get("reasoning", "")
                await self._act(session_id, profile, count, reasoning)
            elif tc.name == "no_action":
                reasoning = tc.arguments.get("reasoning", "")
                _log.info("[%s] no_action — %s", session_id, reasoning)
                await publish("provisioner.no_action", {"session_id": session_id, "reasoning": reasoning})

    async def _llm_predict(self, session_id: str, window: ContextWindow) -> list[ToolCall]:
        pool_stats = await self._svc.pool_stats()
        system_prompt = await build_system_prompt(CATALOG, pool_stats, window)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Based on the context above, decide what to provision."},
        ]

        try:
            response = await self._k2.complete(messages, tools=TOOLS)
        except Exception as exc:
            _log.error("[%s] K2 call failed: %s", session_id, exc)
            return []

        if response.reasoning_content:
            await publish("provisioner.reasoning", {
                "session_id": session_id,
                "reasoning": response.reasoning_content,
            })

        return response.tool_calls

    async def _act(
        self,
        session_id: str,
        profile_name: str,
        count: int,
        reasoning: str,
    ) -> None:
        pool_stats = await self._svc.pool_stats()
        already = pool_stats.get(f"{profile_name}:ready", 0) + pool_stats.get(f"{profile_name}:warming", 0)
        if already >= count:
            _log.info("[%s] Skipping — %d x %s already in pool", session_id, already, profile_name)
            return

        warmed_so_far = self._session_warm_counts.get(session_id, 0)
        remaining_budget = _MAX_POOL_PER_SESSION - warmed_so_far
        if remaining_budget <= 0:
            _log.warning("[%s] Session warm cap reached, skipping", session_id)
            return
        count = min(count, remaining_budget)

        result = await dispatch_tool_call(
            "warm_containers",
            {"profile_name": profile_name, "count": count, "reasoning": reasoning},
            self._svc,
        )
        self._session_warm_counts[session_id] = warmed_so_far + count

        await publish("provisioner.warmed", {"session_id": session_id, **result})

import logging

from ephemeral.docker.catalog import CATALOG
from ephemeral.docker.service import ContainerService
from ephemeral.docker.events import publish
from ephemeral.sessions import get_store

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
        store = get_store()
        window = self._windows.setdefault(session_id, ContextWindow())
        window.add(event)

        content = event.content if isinstance(event.content, str) else str(event.content)
        await store.append_log(session_id, f"Context received: {content}")

        tool_calls = await self._llm_predict(session_id, window)

        for tc in tool_calls:
            if tc.name == "warm_containers":
                profile = tc.arguments.get("profile_name", "")
                tier = tc.arguments.get("resource_tier", "medium")
                count = int(tc.arguments.get("count", 1))
                reasoning = tc.arguments.get("reasoning", "")
                await self._act(session_id, profile, tier, count, reasoning)
            elif tc.name == "no_action":
                reasoning = tc.arguments.get("reasoning", "")
                await store.append_log(session_id, f"No action needed: {reasoning}")
                await store.append_tool_call(session_id, "no_action", tc.arguments, "skipped")

    async def _llm_predict(self, session_id: str, window: ContextWindow) -> list[ToolCall]:
        store = get_store()
        pool_stats = await self._svc.pool_stats()
        system_prompt = await build_system_prompt(CATALOG, pool_stats, window)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Based on the context above, decide what to provision."},
        ]

        await store.append_log(session_id, "Calling K2 Think to reason about provisioning...")

        try:
            response = await self._k2.complete(messages, tools=TOOLS)
        except Exception as exc:
            await store.append_log(session_id, f"K2 call failed: {exc}")
            _log.error("[%s] K2 call failed: %s", session_id, exc)
            return []

        if response.reasoning_content:
            await store.append_log(session_id, f"K2 reasoning: {response.reasoning_content}")

        for tc in response.tool_calls:
            if tc.name == "warm_containers":
                profile = tc.arguments.get("profile_name", "")
                tier = tc.arguments.get("resource_tier", "medium")
                count = tc.arguments.get("count", 1)
                await store.append_log(
                    session_id,
                    f"K2 decided: call warm_containers(profile={profile}, tier={tier}, count={count})"
                )
            elif tc.name == "no_action":
                await store.append_log(session_id, "K2 decided: no_action")

        return response.tool_calls

    async def _act(
        self,
        session_id: str,
        profile_name: str,
        tier: str,
        count: int,
        reasoning: str,
    ) -> None:
        store = get_store()
        pool_stats = await self._svc.pool_stats()
        already = pool_stats.get(f"{profile_name}:{tier}:ready", 0) + pool_stats.get(f"{profile_name}:{tier}:warming", 0)

        if already >= count:
            await store.append_log(
                session_id,
                f"Skipping warm — {already}x {profile_name} [{tier}] already in pool"
            )
            return

        warmed_so_far = self._session_warm_counts.get(session_id, 0)
        remaining_budget = _MAX_POOL_PER_SESSION - warmed_so_far
        if remaining_budget <= 0:
            await store.append_log(session_id, "Session container cap reached, skipping warm")
            return
        count = min(count, remaining_budget)

        await store.append_log(session_id, f"Warming {count}x {profile_name} [{tier}]...")

        try:
            result = await dispatch_tool_call(
                "warm_containers",
                {"profile_name": profile_name, "resource_tier": tier, "count": count, "reasoning": reasoning},
                self._svc,
                session_id=session_id,
            )
            self._session_warm_counts[session_id] = warmed_so_far + count
            await store.append_log(
                session_id,
                f"Warmed {result['count']}x {profile_name} [{tier}] — containers: {result['container_ids']}"
            )
            await store.append_tool_call(session_id, "warm_containers", {
                "profile": profile_name,
                "tier": tier,
                "count": count,
            }, f"warmed {result['count']} containers")

        except Exception as exc:
            await store.append_log(session_id, f"Warm failed for {profile_name} [{tier}]: {exc}")
            await store.append_tool_call(session_id, "warm_containers", {
                "profile": profile_name,
                "tier": tier,
                "count": count,
            }, f"failed: {exc}")
            _log.error("[%s] Warm failed for %s: %s", session_id, profile_name, exc)

import logging
import time

from ephemeral.docker.catalog import CATALOG
from ephemeral.docker.models import Container, ContainerState
from ephemeral.docker.service import ContainerService
from ephemeral.docker.events import publish
from ephemeral.sessions import get_store

from .context import ContextEvent, ContextWindow
from .k2_client import K2Client, ToolCall
from .prompt import build_system_prompt
from .tools import TOOLS, dispatch_tool_call

_log = logging.getLogger("ephemeral.provisioner")

_MAX_POOL_PER_SESSION = 5
_PRUNE_THRESHOLD = 6.0  # weighted units before pruning pass fires
_TIER_WEIGHTS = {"light": 0.5, "medium": 1.0, "heavy": 2.0}

# Tools available only during the pruning pass
_PRUNE_TOOLS = [t for t in TOOLS if t["function"]["name"] in ("kill_containers", "no_action")]


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

        # Fire pruning pass after provisioning if pool is over-provisioned
        containers = await self._svc.list_containers()
        usage = self._compute_usage(containers)
        if usage > _PRUNE_THRESHOLD:
            await store.append_log(
                session_id,
                f"Pool usage {usage:.1f} units exceeds threshold {_PRUNE_THRESHOLD} — triggering pruning pass"
            )
            await self._llm_prune(session_id, containers, window)

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

    def _compute_usage(self, containers: list[Container]) -> float:
        active_states = {
            ContainerState.creating, ContainerState.warming,
            ContainerState.ready, ContainerState.assigned, ContainerState.degraded,
        }
        total = 0.0
        for c in containers:
            if c.state in active_states:
                total += _TIER_WEIGHTS.get(c.spec.resource_tier.value, 1.0)
        return total

    async def _llm_prune(
        self,
        session_id: str,
        containers: list[Container],
        window: ContextWindow,
    ) -> None:
        store = get_store()
        now = time.time()

        killable = [
            c for c in containers
            if c.state in (ContainerState.ready, ContainerState.stopped)
        ]
        if not killable:
            await store.append_log(session_id, "Pruning pass: no killable containers found")
            return

        container_lines = []
        for c in killable:
            age_s = int(now - c.created_at)
            session_tag = c.predicted_for or "none"
            container_lines.append(
                f"  {c.id} | {c.profile_name} [{c.spec.resource_tier.value}]"
                f" | {c.state.value} | age: {age_s}s | predicted_for: {session_tag}"
            )

        usage = self._compute_usage(containers)
        prompt = (
            f"The server pool is over-provisioned (usage: {usage:.1f} units, threshold: {_PRUNE_THRESHOLD}).\n\n"
            f"Killable containers (ready or stopped):\n" + "\n".join(container_lines) + "\n\n"
            f"Recent session context:\n{window.render_for_prompt()}\n\n"
            f"Decide which containers to kill to bring usage below {_PRUNE_THRESHOLD} units. "
            f"Kill idle/old containers first. Never kill a container assigned to an active session. "
            f"Use kill_containers with the IDs to remove, or no_action if nothing should be removed."
        )

        messages = [
            {"role": "system", "content": "You are the EPHEMERAL provisioner managing a container pool."},
            {"role": "user", "content": prompt},
        ]

        await store.append_log(session_id, "Pruning pass: calling K2 to select containers to kill...")

        try:
            response = await self._k2.complete(messages, tools=_PRUNE_TOOLS)
        except Exception as exc:
            await store.append_log(session_id, f"Pruning K2 call failed: {exc}")
            _log.error("[%s] Pruning K2 call failed: %s", session_id, exc)
            return

        if response.reasoning_content:
            await store.append_log(session_id, f"Pruning reasoning: {response.reasoning_content[:300]}")

        for tc in response.tool_calls:
            if tc.name == "kill_containers":
                ids = tc.arguments.get("container_ids", [])
                reasoning = tc.arguments.get("reasoning", "")
                await store.append_log(session_id, f"Pruning: killing {ids} — {reasoning}")
                result = await dispatch_tool_call("kill_containers", tc.arguments, self._svc, session_id=session_id)
                await store.append_tool_call(
                    session_id, "kill_containers",
                    {"container_ids": ids},
                    f"killed {len(result['container_ids'])} containers",
                )
            elif tc.name == "no_action":
                await store.append_log(session_id, f"Pruning: no_action — {tc.arguments.get('reasoning', '')}")

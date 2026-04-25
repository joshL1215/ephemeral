import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from ephemeral.agents.provisioner.agent import ProvisionerAgent, _MAX_POOL_PER_SESSION
from ephemeral.agents.provisioner.context import ContextEvent
from ephemeral.agents.provisioner.k2_client import K2Client, K2Response, ToolCall
from ephemeral.docker.service import ContainerService
from ephemeral.docker.models import Container, ContainerSpec, ContainerState


def _event(text: str) -> ContextEvent:
    return ContextEvent(ts=time.time(), source="agent", content=text)


def _mock_container(profile: str) -> Container:
    return Container(
        id="abc123",
        docker_id="docker456",
        spec=ContainerSpec(profile_name=profile),
        state=ContainerState.ready,
        profile_name=profile,
        created_at=time.time(),
    )


def _make_agent(pool_stats: dict | None = None, tool_calls: list[ToolCall] | None = None):
    svc = MagicMock(spec=ContainerService)
    svc.pool_stats = AsyncMock(return_value=pool_stats or {})
    svc.warm = AsyncMock(return_value=[_mock_container("python-data")])

    k2 = MagicMock(spec=K2Client)
    k2.complete = AsyncMock(return_value=K2Response(
        content=None,
        reasoning_content=None,
        tool_calls=tool_calls or [],
    ))

    agent = ProvisionerAgent(container_service=svc, k2_client=k2)
    return agent, svc, k2


# ------------------------------------------------------------------
# LLM path
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_always_called():
    agent, svc, k2 = _make_agent()
    await agent.on_context_event("s1", _event("anything"))
    k2.complete.assert_called_once()


@pytest.mark.asyncio
async def test_llm_warm_containers_dispatched():
    agent, svc, k2 = _make_agent(tool_calls=[
        ToolCall(id="c1", name="warm_containers",
                 arguments={"profile_name": "python-data", "count": 1, "reasoning": "CSV detected"})
    ])
    await agent.on_context_event("s1", _event("load sales.csv with pandas"))
    svc.warm.assert_awaited_once()
    assert svc.warm.call_args[0][0] == "python-data"


@pytest.mark.asyncio
async def test_llm_no_action_does_not_warm():
    agent, svc, k2 = _make_agent(tool_calls=[
        ToolCall(id="c1", name="no_action", arguments={"reasoning": "pool is fine"})
    ])
    await agent.on_context_event("s1", _event("anything"))
    svc.warm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_failure_does_not_raise():
    agent, svc, k2 = _make_agent()
    k2.complete = AsyncMock(side_effect=Exception("network error"))
    await agent.on_context_event("s1", _event("anything"))
    svc.warm.assert_not_called()


# ------------------------------------------------------------------
# Pool dedup / cap
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skips_warm_when_pool_already_covered():
    agent, svc, k2 = _make_agent(
        pool_stats={"python-data:ready": 1},
        tool_calls=[
            ToolCall(id="c1", name="warm_containers",
                     arguments={"profile_name": "python-data", "count": 1, "reasoning": "test"})
        ],
    )
    await agent.on_context_event("s1", _event("pandas csv"))
    svc.warm.assert_not_called()


@pytest.mark.asyncio
async def test_session_cap_prevents_over_warming():
    agent, svc, k2 = _make_agent(tool_calls=[
        ToolCall(id="c1", name="warm_containers",
                 arguments={"profile_name": "python-data", "count": 1, "reasoning": "test"})
    ])
    agent._session_warm_counts["s1"] = _MAX_POOL_PER_SESSION
    await agent.on_context_event("s1", _event("pandas csv"))
    svc.warm.assert_not_called()


# ------------------------------------------------------------------
# Reasoning forwarded to events
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reasoning_content_published(monkeypatch):
    published = []

    async def fake_publish(event_type, data):
        published.append((event_type, data))

    monkeypatch.setattr("ephemeral.agents.provisioner.agent.publish", fake_publish)

    agent, svc, k2 = _make_agent()
    k2.complete = AsyncMock(return_value=K2Response(
        content=None,
        reasoning_content="I think the user needs data tools.",
        tool_calls=[],
    ))
    await agent.on_context_event("s1", _event("something"))
    assert any(t == "provisioner.reasoning" for t, _ in published)

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ephemeral.agents.provisioner.agent import ProvisionerAgent, _MAX_POOL_PER_SESSION
from ephemeral.agents.provisioner.context import ContextEvent, ContextWindow
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


def _make_agent(pool_stats: dict | None = None):
    svc = MagicMock(spec=ContainerService)
    svc.pool_stats = AsyncMock(return_value=pool_stats or {})
    svc.warm = AsyncMock(return_value=[_mock_container("python-data")])

    k2 = MagicMock(spec=K2Client)
    k2.complete = AsyncMock(return_value=K2Response(
        content=None,
        reasoning_content=None,
        tool_calls=[],
    ))

    agent = ProvisionerAgent(container_service=svc, k2_client=k2)
    return agent, svc, k2


# ------------------------------------------------------------------
# Heuristic tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heuristic_data_keywords_warms_python_data():
    agent, svc, k2 = _make_agent()
    await agent.on_context_event("s1", _event("user wants to load a CSV with pandas and plot it"))
    svc.warm.assert_awaited_once()
    call_args = svc.warm.call_args
    assert call_args[0][0] == "python-data"
    k2.complete.assert_not_called()


@pytest.mark.asyncio
async def test_heuristic_base_keywords_warms_python_base():
    agent, svc, k2 = _make_agent()
    await agent.on_context_event("s1", _event("write a simple script to format a json string"))
    svc.warm.assert_awaited_once()
    call_args = svc.warm.call_args
    assert call_args[0][0] == "python-base"
    k2.complete.assert_not_called()


@pytest.mark.asyncio
async def test_heuristic_no_match_falls_through_to_llm():
    agent, svc, k2 = _make_agent()
    await agent.on_context_event("s1", _event("hello world"))
    k2.complete.assert_called_once()


# ------------------------------------------------------------------
# Pool dedup / cap tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skips_warm_when_pool_already_covered():
    agent, svc, k2 = _make_agent(pool_stats={"python-data:ready": 1})
    await agent.on_context_event("s1", _event("pandas dataframe csv merge groupby"))
    svc.warm.assert_not_called()


@pytest.mark.asyncio
async def test_session_cap_prevents_over_warming():
    agent, svc, k2 = _make_agent()
    agent._session_warm_counts["s1"] = _MAX_POOL_PER_SESSION
    await agent.on_context_event("s1", _event("pandas dataframe csv merge groupby"))
    svc.warm.assert_not_called()


# ------------------------------------------------------------------
# LLM path tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_tool_call_warms_container():
    agent, svc, k2 = _make_agent()
    k2.complete = AsyncMock(return_value=K2Response(
        content=None,
        reasoning_content="The user wants data analysis.",
        tool_calls=[ToolCall(
            id="call_1",
            name="warm_containers",
            arguments={"profile_name": "python-data", "count": 1, "reasoning": "CSV detected"},
        )],
    ))
    await agent.on_context_event("s1", _event("vague context"))
    svc.warm.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_no_action_does_not_warm():
    agent, svc, k2 = _make_agent()
    k2.complete = AsyncMock(return_value=K2Response(
        content=None,
        reasoning_content=None,
        tool_calls=[ToolCall(
            id="call_1",
            name="no_action",
            arguments={"reasoning": "pool is adequate"},
        )],
    ))
    await agent.on_context_event("s1", _event("vague context"))
    svc.warm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_failure_does_not_raise():
    agent, svc, k2 = _make_agent()
    k2.complete = AsyncMock(side_effect=Exception("network error"))
    # should log and swallow, not raise
    await agent.on_context_event("s1", _event("some ambiguous text"))
    svc.warm.assert_not_called()

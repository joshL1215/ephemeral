import pytest
from unittest.mock import AsyncMock, MagicMock

from ephemeral.agents.provisioner.tools import dispatch_tool_call, TOOLS
from ephemeral.docker.service import ContainerService
from ephemeral.docker.models import Container, ContainerSpec, ContainerState
import time


def _mock_container(profile: str) -> Container:
    return Container(
        id="abc123",
        docker_id="docker456",
        spec=ContainerSpec(profile_name=profile),
        state=ContainerState.ready,
        profile_name=profile,
        created_at=time.time(),
    )


def _make_svc(profile: str = "python-base") -> ContainerService:
    svc = MagicMock(spec=ContainerService)
    svc.warm = AsyncMock(return_value=[_mock_container(profile)])
    return svc


@pytest.mark.asyncio
async def test_dispatch_warm_containers():
    svc = _make_svc("python-data")
    result = await dispatch_tool_call(
        "warm_containers",
        {"profile_name": "python-data", "count": 1, "reasoning": "test"},
        svc,
    )
    svc.warm.assert_awaited_once()
    call_kwargs = svc.warm.call_args
    assert call_kwargs[0][0] == "python-data"
    assert call_kwargs[1]["count"] == 1
    assert call_kwargs[1]["spec"].resource_tier.value == "medium"
    assert result["action"] == "warmed"
    assert result["profile"] == "python-data"
    assert result["count"] == 1
    assert result["resource_tier"] == "medium"


@pytest.mark.asyncio
async def test_dispatch_no_action():
    svc = _make_svc()
    result = await dispatch_tool_call("no_action", {"reasoning": "pool is fine"}, svc)
    assert result["action"] == "no_action"
    assert "pool is fine" in result["reasoning"]
    svc.warm.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_unknown_tool():
    svc = _make_svc()
    result = await dispatch_tool_call("nonexistent_tool", {}, svc)
    assert result["action"] == "unknown"


def test_tools_schema_valid():
    names = {t["function"]["name"] for t in TOOLS}
    assert "warm_containers" in names
    assert "no_action" in names


def test_warm_containers_profile_enum():
    warm_tool = next(t for t in TOOLS if t["function"]["name"] == "warm_containers")
    enums = warm_tool["function"]["parameters"]["properties"]["profile_name"]["enum"]
    assert "python-base" in enums
    assert "python-data" in enums


def test_warm_containers_count_bounds():
    warm_tool = next(t for t in TOOLS if t["function"]["name"] == "warm_containers")
    count_schema = warm_tool["function"]["parameters"]["properties"]["count"]
    assert count_schema["minimum"] == 1
    assert count_schema["maximum"] == 5

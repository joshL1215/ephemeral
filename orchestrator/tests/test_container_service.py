import asyncio
import pytest
from unittest.mock import MagicMock
import docker as docker_lib

from ephemeral.docker.service import ContainerService
from ephemeral.docker.models import ContainerSpec, ContainerState
from ephemeral.docker.errors import ContainerNotFoundError, ContainerNotReadyError


def _make_service():
    mock_client = MagicMock(spec=docker_lib.DockerClient)
    return ContainerService(mock_client), mock_client


# ------------------------------------------------------------------
# Unit tests (mocked Docker)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_returns_container():
    service, mock_client = _make_service()

    mock_dc = MagicMock()
    mock_dc.id = "dockerabc123"
    mock_client.containers.create.return_value = mock_dc

    spec = ContainerSpec(profile_name="python-base")
    container = await service.create(spec)

    assert container.profile_name == "python-base"
    assert container.state == ContainerState.creating
    assert container.docker_id == "dockerabc123"
    assert container.id in service._containers


@pytest.mark.asyncio
async def test_start_transitions_to_warming():
    service, mock_client = _make_service()

    mock_dc = MagicMock()
    mock_dc.id = "dockerabc123"
    mock_client.containers.create.return_value = mock_dc
    mock_client.containers.get.return_value = mock_dc

    spec = ContainerSpec(profile_name="python-base")
    container = await service.create(spec)
    await service.start(container.id)

    assert service._containers[container.id].state == ContainerState.warming


@pytest.mark.asyncio
async def test_kill_is_idempotent():
    service, mock_client = _make_service()

    mock_dc = MagicMock()
    mock_dc.id = "dockerabc123"
    mock_client.containers.create.return_value = mock_dc
    mock_client.containers.get.return_value = mock_dc

    spec = ContainerSpec(profile_name="python-base")
    container = await service.create(spec)

    await service.kill(container.id, reason="test")
    await service.kill(container.id, reason="test again")  # should not raise

    assert service._containers[container.id].state == ContainerState.terminated


@pytest.mark.asyncio
async def test_find_match_none_when_empty():
    service, _ = _make_service()
    spec = ContainerSpec(profile_name="python-base")
    result = await service.find_match(spec)
    assert result is None


@pytest.mark.asyncio
async def test_list_containers_empty():
    service, _ = _make_service()
    result = await service.list_containers()
    assert result == []


@pytest.mark.asyncio
async def test_pool_stats_empty():
    service, _ = _make_service()
    stats = await service.pool_stats()
    assert stats == {}


@pytest.mark.asyncio
async def test_find_match_pops_ready_container():
    service, mock_client = _make_service()

    mock_dc = MagicMock()
    mock_dc.id = "dockerabc123"
    mock_dc.status = "running"
    exec_result = MagicMock()
    exec_result.exit_code = 0
    exec_result.output = (b"ok\n", b"")
    mock_dc.exec_run.return_value = exec_result
    mock_client.containers.create.return_value = mock_dc
    mock_client.containers.get.return_value = mock_dc

    spec = ContainerSpec(profile_name="python-base")
    container = await service.create(spec)
    await service.start(container.id)
    await service.wait_ready(container.id)

    assert service._containers[container.id].state == ContainerState.ready

    matched = await service.find_match(spec)
    assert matched is not None
    assert matched.id == container.id
    assert matched.state == ContainerState.assigned

    # pool should now be empty for this sig
    second = await service.find_match(spec)
    assert second is None


@pytest.mark.asyncio
async def test_container_not_found_raises():
    service, _ = _make_service()
    with pytest.raises(ContainerNotFoundError):
        await service.start("nonexistent")


@pytest.mark.asyncio
async def test_exec_returns_result():
    service, mock_client = _make_service()

    mock_dc = MagicMock()
    mock_dc.id = "dockerabc123"
    mock_dc.status = "running"
    probe_result = MagicMock()
    probe_result.exit_code = 0
    probe_result.output = (b"ok\n", b"")

    exec_call_result = MagicMock()
    exec_call_result.exit_code = 0
    exec_call_result.output = (b"hello\n", b"")

    mock_dc.exec_run.side_effect = [probe_result, exec_call_result]
    mock_client.containers.create.return_value = mock_dc
    mock_client.containers.get.return_value = mock_dc

    spec = ContainerSpec(profile_name="python-base")
    c = await service.create(spec)
    await service.start(c.id)
    await service.wait_ready(c.id)

    result = await service.exec(c.id, "print('hello')")
    assert result.exit_code == 0
    assert "hello" in result.stdout


# ------------------------------------------------------------------
# Integration tests (require real Docker + gVisor)
# ------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle():
    client = docker_lib.from_env()
    service = ContainerService(client)

    containers = await service.warm("python-base", count=1)
    assert len(containers) == 1
    c = containers[0]
    assert c.state == ContainerState.ready

    matched = await service.find_match(ContainerSpec(profile_name="python-base"))
    assert matched is not None
    assert matched.id == c.id

    result = await service.exec(matched.id, "print('integration-test')")
    assert result.exit_code == 0
    assert "integration-test" in result.stdout

    await service.kill(matched.id, reason="test done")
    assert service._containers[matched.id].state == ContainerState.terminated


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_gvisor_runtime():
    client = docker_lib.from_env()
    service = ContainerService(client)

    containers = await service.warm("python-base", count=1)
    c = containers[0]

    def _inspect():
        dc = client.containers.get(c.docker_id)
        return dc.attrs

    attrs = await asyncio.to_thread(_inspect)
    runtime = attrs.get("HostConfig", {}).get("Runtime", "")
    assert runtime == "runsc", f"Expected runsc, got {runtime!r}"

    await service.kill(c.id, reason="gvisor check done")

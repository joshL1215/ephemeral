import asyncio
import logging
import time
import uuid

import docker
import docker.errors as docker_errors

from .catalog import get_profile
from .errors import (
    ContainerNotFoundError,
    ContainerNotReadyError,
    ExecutionTimeoutError,
)
from .events import publish
from .models import Container, ContainerSpec, ContainerState, ExecResult, ResourceTier

_log = logging.getLogger("ephemeral.docker")

_RUNTIME = "runsc"


class ContainerService:
    def __init__(self, docker_client: docker.DockerClient) -> None:
        self._client = docker_client
        self._containers: dict[str, Container] = {}
        self._ready_by_signature: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(self, spec: ContainerSpec) -> Container:
        profile = get_profile(spec.profile_name)
        cid = uuid.uuid4().hex[:12]

        def _create():
            return self._client.containers.create(
                image=profile.image,
                command=profile.keepalive_command,
                environment=spec.env,
                user=profile.user,
                runtime=_RUNTIME,
                detach=True,
                mem_limit=f"{spec.memory_mb}m",
                nano_cpus=int(spec.cpu_quota * 1e9),
                labels={
                    "ephemeral.id": cid,
                    "ephemeral.profile": spec.profile_name,
                    "ephemeral.tier": spec.resource_tier.value,
                },
            )

        try:
            docker_container = await asyncio.to_thread(_create)
        except Exception as exc:
            _log.error("Failed to create container for profile %s: %s", spec.profile_name, exc)
            raise

        container = Container(
            id=cid,
            docker_id=docker_container.id,
            spec=spec,
            state=ContainerState.creating,
            profile_name=spec.profile_name,
            created_at=time.time(),
        )

        async with self._lock:
            self._containers[cid] = container

        await publish("container.created", {
            "id": cid,
            "profile": spec.profile_name,
            "resource_tier": spec.resource_tier.value,
            "memory_mb": spec.memory_mb,
            "cpu_quota": spec.cpu_quota,
        })
        _log.info("Created container %s (profile=%s)", cid, spec.profile_name)
        return container

    async def start(self, container_id: str) -> None:
        container = self._get(container_id)

        def _start():
            dc = self._client.containers.get(container.docker_id)
            dc.start()

        try:
            await asyncio.to_thread(_start)
        except Exception as exc:
            _log.error("Failed to start container %s: %s", container_id, exc)
            await self._mark_terminated(container_id)
            raise

        async with self._lock:
            self._containers[container_id].state = ContainerState.warming

        await publish("container.started", {
            "id": container_id,
            "profile": container.profile_name,
            "resource_tier": container.spec.resource_tier.value,
            "state": ContainerState.warming.value,
        })
        _log.info("Started container %s → warming", container_id)

    async def wait_ready(self, container_id: str, timeout_s: float = 30.0) -> None:
        container = self._get(container_id)
        deadline = time.monotonic() + timeout_s

        def _probe():
            dc = self._client.containers.get(container.docker_id)
            dc.reload()
            if dc.status != "running":
                return False
            result = dc.exec_run(["python", "-c", "print('ok')"], demux=True)
            stdout_data = result.output[0] or b""
            return result.exit_code == 0 and b"ok" in stdout_data

        while True:
            if time.monotonic() > deadline:
                await self._mark_terminated(container_id)
                raise ContainerNotReadyError(
                    f"Container {container_id} did not become ready within {timeout_s}s"
                )
            try:
                ready = await asyncio.to_thread(_probe)
            except docker_errors.NotFound:
                await self._mark_terminated(container_id)
                raise ContainerNotReadyError(f"Container {container_id} disappeared")
            except Exception as exc:
                _log.debug("Probe error for %s: %s", container_id, exc)
                ready = False

            if ready:
                break
            await asyncio.sleep(0.5)

        now = time.time()
        sig = container.spec.signature()

        async with self._lock:
            self._containers[container_id].state = ContainerState.ready
            self._containers[container_id].ready_at = now
            self._ready_by_signature.setdefault(sig, []).append(container_id)

        container = self._containers[container_id]
        await publish("container.ready", {
            "id": container_id,
            "profile": container.profile_name,
            "resource_tier": container.spec.resource_tier.value,
            "state": ContainerState.ready.value,
            "ready_at": now,
        })
        _log.info("Container %s is ready (sig=%s)", container_id, sig)

    async def warm(self, profile_name: str, count: int = 1, spec: ContainerSpec | None = None) -> list[Container]:
        spec = spec or ContainerSpec(profile_name=profile_name)

        async def _one():
            c = await self.create(spec)
            await self.start(c.id)
            await self.wait_ready(c.id)
            return self._containers[c.id]

        results = await asyncio.gather(*[_one() for _ in range(count)])
        return list(results)

    async def find_match(self, spec: ContainerSpec) -> Container | None:
        sig = spec.signature()
        async with self._lock:
            queue = self._ready_by_signature.get(sig, [])
            while queue:
                cid = queue.pop(0)
                if cid in self._containers and self._containers[cid].state == ContainerState.ready:
                    self._containers[cid].state = ContainerState.assigned
                    return self._containers[cid]
        return None

    async def exec(self, container_id: str, code: str, language: str = "python") -> ExecResult:
        container = self._get(container_id)
        if container.state not in (ContainerState.ready, ContainerState.assigned):
            raise ContainerNotReadyError(f"Container {container_id} is not ready/assigned")

        if language == "python":
            cmd = ["python", "-c", code]
        else:
            cmd = ["sh", "-c", code]

        start_ms = time.monotonic()

        def _exec():
            dc = self._client.containers.get(container.docker_id)
            return dc.exec_run(cmd, demux=True)

        try:
            result = await asyncio.to_thread(_exec)
        except Exception as exc:
            _log.error("exec failed on %s: %s", container_id, exc)
            raise

        duration_ms = int((time.monotonic() - start_ms) * 1000)
        stdout = (result.output[0] or b"").decode(errors="replace")
        stderr = (result.output[1] or b"").decode(errors="replace")

        exec_result = ExecResult(
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )
        await publish("container.exec", {"id": container_id, "exit_code": result.exit_code})
        return exec_result

    async def install_packages(self, container_id: str, packages: list[str]) -> ExecResult:
        if not packages:
            return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0)
        cmd_str = "pip install " + " ".join(packages)
        return await self.exec(container_id, cmd_str, language="sh")

    async def kill(self, container_id: str, reason: str = "") -> None:
        if container_id not in self._containers:
            return

        container = self._containers[container_id]
        if container.state == ContainerState.terminated:
            return

        async with self._lock:
            self._containers[container_id].state = ContainerState.terminating

        def _kill():
            try:
                dc = self._client.containers.get(container.docker_id)
                dc.remove(force=True)
            except docker_errors.NotFound:
                pass

        try:
            await asyncio.to_thread(_kill)
        except Exception as exc:
            _log.warning("Error killing container %s: %s", container_id, exc)

        await self._mark_terminated(container_id)
        await publish("container.killed", {"id": container_id, "reason": reason})
        _log.info("Killed container %s (reason=%s)", container_id, reason)

    async def list_containers(self, state_filter: ContainerState | None = None) -> list[Container]:
        await self._sync_from_docker()
        async with self._lock:
            containers = list(self._containers.values())
        if state_filter is not None:
            containers = [c for c in containers if c.state == state_filter]
        return containers

    async def pool_stats(self) -> dict[str, int]:
        await self._sync_from_docker()
        async with self._lock:
            containers = list(self._containers.values())
        stats: dict[str, int] = {}
        for c in containers:
            key = f"{c.profile_name}:{c.spec.resource_tier.value}:{c.state.value}"
            stats[key] = stats.get(key, 0) + 1
        return stats

    @staticmethod
    def _docker_health(dc) -> str | None:
        """Returns Docker health status string or None if no health check."""
        try:
            status = dc.attrs["State"]["Health"]["Status"]
            _log.debug("Health check for %s: %s", dc.labels.get("ephemeral.id"), status)
            return status
        except (KeyError, TypeError):
            _log.debug("No health info for %s (state=%s)", dc.labels.get("ephemeral.id"), dc.attrs.get("State", {}).get("Status"))
            return None

    async def _sync_from_docker(self) -> None:
        """Pull live Docker state and update in-memory records to match."""
        def _list():
            containers = self._client.containers.list(
                all=True, filters={"label": "ephemeral.id"}
            )
            for dc in containers:
                dc.reload()  # ensure attrs (including Health) are fresh
            return {
                dc.labels["ephemeral.id"]: dc
                for dc in containers
                if "ephemeral.id" in dc.labels
            }

        try:
            live = await asyncio.to_thread(_list)
        except Exception as exc:
            _log.debug("_sync_from_docker failed: %s", exc)
            return

        async with self._lock:
            for cid, container in list(self._containers.items()):
                if container.state == ContainerState.terminated:
                    continue
                dc = live.get(cid)
                if dc is None or dc.status in ("exited", "dead", "removing"):
                    container.state = ContainerState.terminated
                    sig = container.spec.signature()
                    queue = self._ready_by_signature.get(sig, [])
                    if cid in queue:
                        queue.remove(cid)
                    _log.info("Sync: container %s disappeared, marked terminated", cid)
                    continue

                health = self._docker_health(dc)
                is_unhealthy = health == "unhealthy"
                is_healthy = dc.status == "running" and not is_unhealthy

                if is_unhealthy and container.state != ContainerState.degraded:
                    container.state = ContainerState.degraded
                    sig = container.spec.signature()
                    queue = self._ready_by_signature.get(sig, [])
                    if cid in queue:
                        queue.remove(cid)
                    _log.info("Sync: container %s is unhealthy, marked degraded", cid)
                elif is_healthy and container.state in (ContainerState.warming, ContainerState.degraded):
                    container.state = ContainerState.ready
                    container.ready_at = container.ready_at or time.time()
                    sig = container.spec.signature()
                    if cid not in self._ready_by_signature.get(sig, []):
                        self._ready_by_signature.setdefault(sig, []).append(cid)
                    _log.info("Sync: container %s promoted to ready", cid)

    async def reconcile(self) -> int:
        """Scan Docker for containers with our labels and rebuild in-memory state.
        Called once at startup. Returns the number of containers recovered."""

        def _list():
            return self._client.containers.list(
                all=True,
                filters={"label": "ephemeral.id"},
            )

        try:
            docker_containers = await asyncio.to_thread(_list)
        except Exception as exc:
            _log.warning("Reconcile failed to list Docker containers: %s", exc)
            return 0

        recovered = 0
        for dc in docker_containers:
            cid = dc.labels.get("ephemeral.id")
            profile_name = dc.labels.get("ephemeral.profile")
            if not cid or not profile_name:
                continue

            # Skip states we can't recover meaningfully
            if dc.status in ("removing", "dead"):
                continue

            # Map Docker status → ContainerState, respecting health check
            health = self._docker_health(dc)
            if dc.status == "running" and health == "unhealthy":
                state = ContainerState.degraded
            elif dc.status == "running":
                state = ContainerState.ready
            elif dc.status in ("created", "paused", "restarting"):
                state = ContainerState.warming
            else:
                state = ContainerState.terminated

            if state == ContainerState.terminated:
                continue

            try:
                tier_label = dc.labels.get("ephemeral.tier", "medium")
                spec = ContainerSpec(
                    profile_name=profile_name,
                    resource_tier=ResourceTier(tier_label),
                )
            except Exception:
                spec = ContainerSpec(profile_name=profile_name)

            container = Container(
                id=cid,
                docker_id=dc.id,
                spec=spec,
                state=state,
                profile_name=profile_name,
                created_at=time.time(),
                ready_at=time.time() if state == ContainerState.ready else None,
            )

            async with self._lock:
                self._containers[cid] = container
                if state == ContainerState.ready:
                    sig = spec.signature()
                    self._ready_by_signature.setdefault(sig, []).append(cid)

            recovered += 1
            _log.info("Reconciled container %s (profile=%s state=%s)", cid, profile_name, state.value)

        _log.info("Reconcile complete: recovered %d containers", recovered)
        return recovered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, container_id: str) -> Container:
        try:
            return self._containers[container_id]
        except KeyError:
            raise ContainerNotFoundError(f"Container not found: {container_id}")

    async def _mark_terminated(self, container_id: str) -> None:
        async with self._lock:
            if container_id in self._containers:
                self._containers[container_id].state = ContainerState.terminated
                sig = self._containers[container_id].spec.signature()
                queue = self._ready_by_signature.get(sig, [])
                if container_id in queue:
                    queue.remove(container_id)

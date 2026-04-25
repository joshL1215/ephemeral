import logging
import time
from dataclasses import dataclass

from ephemeral.docker.service import ContainerService
from ephemeral.mcp.router import RoutingResult

_log = logging.getLogger("ephemeral.mcp.executor")


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    container_id: str
    profile: str
    resource_tier: str
    matched: str
    installed_packages: list[str]
    duration_ms: int


async def execute(
    code: str,
    language: str,
    routing: RoutingResult,
    container_service: ContainerService,
) -> ExecutionResult:
    t0 = time.monotonic()
    try:
        result = await container_service.exec(routing.container_id, code, language=language)
    finally:
        # Kill after use — logs are preserved in exec_history, container is shown as terminated in UI
        await container_service.kill(routing.container_id, reason="execution complete")

    duration_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "Executed on %s (exit=%d, matched=%s, %dms)",
        routing.container_id, result.exit_code, routing.matched, duration_ms,
    )
    return ExecutionResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        container_id=routing.container_id,
        profile=routing.profile,
        resource_tier=routing.resource_tier,
        matched=routing.matched,
        installed_packages=routing.installed_packages,
        duration_ms=duration_ms,
    )

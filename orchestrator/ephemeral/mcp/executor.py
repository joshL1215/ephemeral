import logging
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


async def execute(
    code: str,
    language: str,
    routing: RoutingResult,
    container_service: ContainerService,
) -> ExecutionResult:
    result = await container_service.exec(routing.container_id, code, language=language)
    _log.info(
        "Executed on %s (exit=%d, matched=%s)",
        routing.container_id, result.exit_code, routing.matched,
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
    )

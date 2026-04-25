import hashlib
from enum import Enum
from pydantic import BaseModel


class ContainerState(str, Enum):
    creating = "creating"
    warming = "warming"
    ready = "ready"
    assigned = "assigned"
    terminating = "terminating"
    terminated = "terminated"


class ContainerSpec(BaseModel):
    profile_name: str
    extra_packages: list[str] = []
    env: dict[str, str] = {}
    timeout_s: int = 30
    memory_mb: int = 512
    cpu_quota: float = 1.0

    def signature(self) -> str:
        key = self.profile_name + ":" + ",".join(sorted(self.extra_packages))
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class Container(BaseModel):
    id: str
    docker_id: str
    spec: ContainerSpec
    state: ContainerState
    profile_name: str
    created_at: float
    ready_at: float | None = None
    assigned_to: str | None = None


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

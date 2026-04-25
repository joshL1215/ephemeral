import hashlib
from enum import Enum
from pydantic import BaseModel, model_validator


class ContainerState(str, Enum):
    creating = "creating"
    warming = "warming"
    ready = "ready"
    assigned = "assigned"
    degraded = "degraded"
    stopped = "stopped"      # exited in Docker, still exists, pruneable
    terminating = "terminating"
    terminated = "terminated"  # removed from Docker, disappears


class ResourceTier(str, Enum):
    light = "light"
    medium = "medium"
    heavy = "heavy"


_TIER_DEFAULTS = {
    ResourceTier.light:  {"memory_mb": 256,  "cpu_quota": 0.5},
    ResourceTier.medium: {"memory_mb": 512,  "cpu_quota": 1.0},
    ResourceTier.heavy:  {"memory_mb": 2048, "cpu_quota": 2.0},
}


class ContainerSpec(BaseModel):
    profile_name: str
    resource_tier: ResourceTier = ResourceTier.medium
    extra_packages: list[str] = []
    env: dict[str, str] = {}
    timeout_s: int = 30
    memory_mb: int = 512
    cpu_quota: float = 1.0
    predicted_for: str | None = None

    @model_validator(mode="after")
    def apply_tier_defaults(self) -> "ContainerSpec":
        defaults = _TIER_DEFAULTS[self.resource_tier]
        # only apply tier defaults if the fields were not explicitly set
        if self.memory_mb == 512 and self.cpu_quota == 1.0:
            self.memory_mb = defaults["memory_mb"]
            self.cpu_quota = defaults["cpu_quota"]
        return self

    def signature(self) -> str:
        key = self.profile_name + ":" + self.resource_tier.value + ":" + ",".join(sorted(self.extra_packages))
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
    predicted_for: str | None = None


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

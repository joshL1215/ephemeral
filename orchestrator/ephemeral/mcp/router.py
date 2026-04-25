import ast
import logging
from dataclasses import dataclass

from ephemeral.docker.catalog import CATALOG, get_profile
from ephemeral.docker.models import ContainerSpec, ContainerState, ResourceTier
from ephemeral.docker.service import ContainerService

_log = logging.getLogger("ephemeral.mcp.router")

# Map import names → profile that covers them
_PROFILE_PACKAGES: dict[str, set[str]] = {
    p.name: set(p.packages) for p in CATALOG
}

# Import aliases that map to package names
_IMPORT_ALIASES: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
}

# Keywords in code that suggest heavy compute
_HEAVY_SIGNALS = {
    "fit(", "train(", "GridSearchCV", "RandomizedSearchCV",
    "cross_val_score", "Pipeline(", ".fit_transform(",
    "epochs", "batch_size", "DataLoader",
}

_MEDIUM_SIGNALS = {
    "pandas", "DataFrame", "read_csv", "read_parquet",
    "groupby", "merge", "pivot_table", "numpy", "scipy",
}


def extract_imports(code: str) -> set[str]:
    """Parse code with AST and return all top-level module names imported."""
    imports: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    # Resolve aliases
    resolved: set[str] = set()
    for name in imports:
        resolved.add(_IMPORT_ALIASES.get(name, name))
    return resolved


def missing_packages(imports: set[str], profile_name: str) -> list[str]:
    """Return packages imported by code but not covered by the profile."""
    covered = _PROFILE_PACKAGES.get(profile_name, set())
    stdlib = _stdlib_modules()
    return [
        pkg for pkg in imports
        if pkg not in covered and pkg not in stdlib
    ]


def best_profile(imports: set[str]) -> str:
    """Pick the profile that covers the most imports. Falls back to python-base."""
    scores: dict[str, int] = {}
    for profile in CATALOG:
        covered = _PROFILE_PACKAGES[profile.name]
        scores[profile.name] = len(imports & covered)

    return max(scores, key=lambda k: scores[k])


def infer_tier(code: str) -> ResourceTier:
    """Infer resource tier from code signals."""
    if any(sig in code for sig in _HEAVY_SIGNALS):
        return ResourceTier.heavy
    if any(sig in code for sig in _MEDIUM_SIGNALS):
        return ResourceTier.medium
    return ResourceTier.light


@dataclass
class RoutingResult:
    container_id: str
    profile: str
    resource_tier: str
    matched: str  # "session" | "pool" | "topup" | "ondemand"
    installed_packages: list[str]


async def route(
    code: str,
    session_id: str | None,
    hint_tier: str | None,
    container_service: ContainerService,
) -> RoutingResult:
    """Find or create a container for this code workload."""
    imports = extract_imports(code)
    profile_name = best_profile(imports)
    try:
        tier = ResourceTier(hint_tier) if hint_tier else infer_tier(code)
    except ValueError:
        tier = infer_tier(code)
    spec = ContainerSpec(profile_name=profile_name, resource_tier=tier)

    # 1. Exact match — prefer session-tagged container
    container = await container_service.find_match(spec, session_id=session_id)
    if container:
        matched = "session" if container.predicted_for == session_id else "pool"
        missing = missing_packages(imports, profile_name)
        if missing:
            _log.info("Top-up installing %s on container %s", missing, container.id)
            await container_service.install_packages(container.id, missing)
            matched = "topup"
        _log.info("Routed to container %s via %s match", container.id, matched)
        return RoutingResult(
            container_id=container.id,
            profile=profile_name,
            resource_tier=tier.value,
            matched=matched,
            installed_packages=missing,
        )

    # 2. Same profile, any tier — top-up missing packages
    fallback_spec = ContainerSpec(profile_name=profile_name, resource_tier=ResourceTier.medium)
    for fallback_tier in [ResourceTier.heavy, ResourceTier.light]:
        fallback_spec = ContainerSpec(profile_name=profile_name, resource_tier=fallback_tier)
        container = await container_service.find_match(fallback_spec, session_id=session_id)
        if container:
            break

    if container:
        missing = missing_packages(imports, profile_name)
        if missing:
            _log.info("Top-up installing %s on container %s", missing, container.id)
            await container_service.install_packages(container.id, missing)
        return RoutingResult(
            container_id=container.id,
            profile=profile_name,
            resource_tier=container.spec.resource_tier.value,
            matched="topup" if missing else "pool",
            installed_packages=missing,
        )

    # 3. On-demand warm — slow path
    _log.warning("No ready container for %s [%s], warming on-demand", profile_name, tier.value)
    containers = await container_service.warm(profile_name, count=1, spec=spec)
    container = containers[0]
    container = await container_service.find_match(spec, session_id=session_id)
    if not container:
        raise RuntimeError(f"On-demand warm succeeded but find_match returned None for {profile_name}")

    missing = missing_packages(imports, profile_name)
    if missing:
        await container_service.install_packages(container.id, missing)

    return RoutingResult(
        container_id=container.id,
        profile=profile_name,
        resource_tier=tier.value,
        matched="ondemand",
        installed_packages=missing,
    )


def _stdlib_modules() -> set[str]:
    """Return the set of stdlib module names so we don't try to pip-install them."""
    import sys
    if sys.version_info >= (3, 10):
        return sys.stdlib_module_names  # type: ignore[attr-defined]
    # Fallback for 3.8/3.9
    return {
        "os", "sys", "re", "json", "time", "math", "random", "datetime",
        "collections", "itertools", "functools", "pathlib", "typing",
        "io", "abc", "copy", "enum", "dataclasses", "contextlib",
        "logging", "threading", "asyncio", "subprocess", "shutil",
        "tempfile", "hashlib", "base64", "uuid", "struct", "socket",
        "http", "urllib", "email", "html", "xml", "csv", "sqlite3",
        "unittest", "traceback", "inspect", "ast", "dis", "gc",
        "warnings", "weakref", "operator", "string", "textwrap",
        "pprint", "pickle", "shelve", "gzip", "zipfile", "tarfile",
    }

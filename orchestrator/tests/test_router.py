import pytest
from unittest.mock import AsyncMock, MagicMock
import time

from ephemeral.docker.models import Container, ContainerSpec, ContainerState, ResourceTier
from ephemeral.mcp.router import (
    extract_imports,
    best_profile,
    missing_packages,
    infer_tier,
    route,
    RoutingResult,
)


# ------------------------------------------------------------------
# extract_imports
# ------------------------------------------------------------------

def test_extract_imports_simple():
    code = "import pandas as pd\nimport numpy as np"
    result = extract_imports(code)
    assert "pandas" in result
    assert "numpy" in result


def test_extract_imports_from_style():
    code = "from sklearn.linear_model import LinearRegression\nfrom collections import defaultdict"
    result = extract_imports(code)
    assert "scikit-learn" in result  # sklearn alias resolved
    assert "collections" in result


def test_extract_imports_alias_resolution():
    code = "import cv2\nfrom PIL import Image\nimport bs4"
    result = extract_imports(code)
    assert "opencv-python" in result
    assert "Pillow" in result
    assert "beautifulsoup4" in result


def test_extract_imports_syntax_error_returns_empty():
    result = extract_imports("def broken(:")
    assert result == set()


def test_extract_imports_empty_code():
    result = extract_imports("")
    assert result == set()


def test_extract_imports_no_imports():
    result = extract_imports("x = 1 + 2\nprint(x)")
    assert result == set()


# ------------------------------------------------------------------
# best_profile
# ------------------------------------------------------------------

def test_best_profile_data_imports():
    imports = {"pandas", "numpy", "matplotlib"}
    assert best_profile(imports) == "python-data"


def test_best_profile_no_data_imports():
    imports = {"json", "os", "sys"}
    assert best_profile(imports) == "python-base"


def test_best_profile_mixed_prefers_data():
    imports = {"pandas", "requests", "json"}
    assert best_profile(imports) == "python-data"


# ------------------------------------------------------------------
# missing_packages
# ------------------------------------------------------------------

def test_missing_packages_covered():
    imports = {"pandas", "numpy"}
    assert missing_packages(imports, "python-data") == []


def test_missing_packages_extra_needed():
    imports = {"pandas", "xgboost"}
    result = missing_packages(imports, "python-data")
    assert "xgboost" in result
    assert "pandas" not in result


def test_missing_packages_stdlib_excluded():
    imports = {"os", "sys", "json", "xgboost"}
    result = missing_packages(imports, "python-data")
    assert "os" not in result
    assert "sys" not in result
    assert "json" not in result
    assert "xgboost" in result


# ------------------------------------------------------------------
# infer_tier
# ------------------------------------------------------------------

def test_infer_tier_heavy_signals():
    code = "model.fit(X_train, y_train)"
    assert infer_tier(code) == ResourceTier.heavy


def test_infer_tier_medium_signals():
    code = "import pandas\ndf = pandas.read_csv('data.csv')"
    assert infer_tier(code) == ResourceTier.medium


def test_infer_tier_light():
    code = "x = 1 + 2\nprint(x)"
    assert infer_tier(code) == ResourceTier.light


def test_infer_tier_gridsearch_is_heavy():
    code = "gs = GridSearchCV(estimator, param_grid)"
    assert infer_tier(code) == ResourceTier.heavy


# ------------------------------------------------------------------
# route — mocked ContainerService
# ------------------------------------------------------------------

def _make_container(cid="abc123", profile="python-data", tier=ResourceTier.medium, predicted_for=None):
    return Container(
        id=cid,
        docker_id="docker_" + cid,
        spec=ContainerSpec(profile_name=profile, resource_tier=tier, predicted_for=predicted_for),
        state=ContainerState.assigned,
        profile_name=profile,
        created_at=time.time(),
        ready_at=time.time(),
        predicted_for=predicted_for,
    )


def _mock_service(find_return=None, warm_return=None):
    svc = MagicMock()
    svc.find_match = AsyncMock(return_value=find_return)
    if warm_return is not None:
        svc.warm = AsyncMock(return_value=warm_return)
    svc.install_packages = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_route_session_match():
    container = _make_container(predicted_for="sess-1")
    svc = _mock_service(find_return=container)

    result = await route("import pandas as pd", "sess-1", None, svc)

    assert result.container_id == "abc123"
    assert result.matched == "session"
    assert result.installed_packages == []


@pytest.mark.asyncio
async def test_route_pool_match():
    container = _make_container(predicted_for="sess-other")
    svc = _mock_service(find_return=container)

    result = await route("import pandas as pd", "sess-1", None, svc)

    assert result.matched == "pool"


@pytest.mark.asyncio
async def test_route_no_match_triggers_ondemand():
    container = _make_container()
    svc = MagicMock()
    # First find_match (exact) returns None, second (after warm) returns container
    svc.find_match = AsyncMock(side_effect=[None, None, None, container])
    svc.warm = AsyncMock(return_value=[container])
    svc.install_packages = AsyncMock()

    result = await route("import pandas as pd", "sess-1", None, svc)

    assert result.matched == "ondemand"
    svc.warm.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_installs_missing_packages():
    container = _make_container()
    svc = MagicMock()
    svc.find_match = AsyncMock(return_value=container)
    svc.install_packages = AsyncMock()

    # xgboost is not in python-data profile
    result = await route("import pandas\nimport xgboost", "sess-1", None, svc)

    svc.install_packages.assert_awaited_once()
    assert "xgboost" in result.installed_packages
    assert result.matched in ("session", "pool", "topup")


@pytest.mark.asyncio
async def test_route_hint_tier_respected():
    container = _make_container(tier=ResourceTier.heavy)
    svc = _mock_service(find_return=container)

    result = await route("print('hello')", "sess-1", "heavy", svc)

    # find_match should have been called with a heavy spec
    call_spec = svc.find_match.call_args[0][0]
    assert call_spec.resource_tier == ResourceTier.heavy


@pytest.mark.asyncio
async def test_route_base_profile_for_simple_code():
    container = _make_container(profile="python-base", tier=ResourceTier.light)
    svc = _mock_service(find_return=container)

    result = await route("x = 1 + 2\nprint(x)", "sess-1", None, svc)

    call_spec = svc.find_match.call_args[0][0]
    assert call_spec.profile_name == "python-base"


# ------------------------------------------------------------------
# predicted_for propagation via find_match
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_match_prefers_session_tagged(tmp_path):
    """find_match should return session-tagged container before generic pool entry."""
    import docker as docker_lib
    from ephemeral.docker.service import ContainerService

    # We test the logic directly — don't need real Docker
    svc = ContainerService.__new__(ContainerService)
    import asyncio
    svc._lock = asyncio.Lock()

    spec = ContainerSpec(profile_name="python-data", resource_tier=ResourceTier.medium)
    sig = spec.signature()

    generic = Container(
        id="generic1", docker_id="d1", spec=spec,
        state=ContainerState.ready, profile_name="python-data",
        created_at=time.time(), predicted_for=None,
    )
    session_tagged = Container(
        id="tagged1", docker_id="d2", spec=spec,
        state=ContainerState.ready, profile_name="python-data",
        created_at=time.time(), predicted_for="sess-xyz",
    )

    svc._containers = {"generic1": generic, "tagged1": session_tagged}
    svc._ready_by_signature = {sig: ["generic1", "tagged1"]}

    result = await svc.find_match(spec, session_id="sess-xyz")

    assert result is not None
    assert result.id == "tagged1"
    assert result.predicted_for == "sess-xyz"

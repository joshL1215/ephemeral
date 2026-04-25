import pytest
from ephemeral.agents.provisioner.k2_client import k2_client_from_env
from ephemeral.agents.provisioner.tools import TOOLS
from ephemeral.agents.provisioner.prompt import build_system_prompt
from ephemeral.agents.provisioner.context import ContextEvent, ContextWindow
from ephemeral.docker.catalog import CATALOG
import time


def _window(*texts: str) -> ContextWindow:
    w = ContextWindow()
    for t in texts:
        w.add(ContextEvent(ts=time.time(), source="agent", content=t))
    return w


async def _decide(client, context_texts: list[str], pool_stats: dict | None = None):
    window = _window(*context_texts)
    prompt = await build_system_prompt(CATALOG, pool_stats or {}, window)
    response = await client.complete(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Based on the context above, decide what to provision."},
        ],
        tools=TOOLS,
    )
    print("reasoning:", response.reasoning_content)
    print("tool_calls:", response.tool_calls)
    return response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_basic_completion():
    client = k2_client_from_env()
    response = await client.complete([
        {"role": "user", "content": "Say hello in one word."}
    ])
    assert response.content or response.tool_calls
    print("content:", response.content)
    print("reasoning:", response.reasoning_content)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_reasoning_content_present():
    client = k2_client_from_env()
    response = await client.complete([
        {"role": "user", "content": "What is 2 + 2? Think step by step."}
    ])
    print("reasoning:", response.reasoning_content)
    print("content:", response.content)
    assert response.reasoning_content, "Expected reasoning_content parsed from <think> tags"


# ------------------------------------------------------------------
# Profile selection
# ------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_selects_python_data_for_csv_work():
    client = k2_client_from_env()
    response = await _decide(client, ["I need to load sales.csv with pandas and plot quarterly trends"])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["profile_name"] == "python-data"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_selects_python_base_for_simple_script():
    client = k2_client_from_env()
    response = await _decide(client, ["write a script to reformat this JSON string and call a REST API"])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["profile_name"] == "python-base"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_action_when_pool_already_covered():
    client = k2_client_from_env()
    response = await _decide(
        client,
        ["loading a CSV with pandas"],
        pool_stats={"python-data:medium:ready": 2},
    )
    assert response.tool_calls
    assert response.tool_calls[0].name == "no_action"


# ------------------------------------------------------------------
# Resource tier reasoning
# ------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_heavy_tier_for_sklearn_training():
    client = k2_client_from_env()
    response = await _decide(client, [
        "training a random forest classifier on 10GB of sensor data using scikit-learn",
        "need cross-validation with 5 folds across 500 estimators",
    ])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["resource_tier"] == "heavy", (
        f"Expected heavy for ML training, got {tc.arguments.get('resource_tier')}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_light_tier_for_simple_api_call():
    client = k2_client_from_env()
    response = await _decide(client, [
        "just need to make a quick HTTP request to fetch some JSON and print a field",
    ])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["resource_tier"] == "light", (
        f"Expected light for simple API call, got {tc.arguments.get('resource_tier')}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_medium_tier_for_moderate_data_work():
    client = k2_client_from_env()
    response = await _decide(client, [
        "load a CSV with pandas, compute a rolling mean, and generate a matplotlib chart",
    ])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["resource_tier"] == "medium", (
        f"Expected medium for moderate data work, got {tc.arguments.get('resource_tier')}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heavy_tier_for_numerical_simulation():
    client = k2_client_from_env()
    response = await _decide(client, [
        "running a monte carlo simulation with scipy across 1 million iterations",
    ])
    assert response.tool_calls
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["resource_tier"] == "heavy", (
        f"Expected heavy for simulation, got {tc.arguments.get('resource_tier')}"
    )

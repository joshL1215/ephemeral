import pytest
from ephemeral.agents.provisioner.k2_client import k2_client_from_env
from ephemeral.agents.provisioner.tools import TOOLS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_basic_completion():
    client = k2_client_from_env()
    response = await client.complete([
        {"role": "user", "content": "Say hello in one word."}
    ])
    assert response.content or response.tool_calls
    # reasoning_content may or may not be present depending on model mode
    print("content:", response.content)
    print("reasoning:", response.reasoning_content)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_tool_calling():
    client = k2_client_from_env()
    response = await client.complete(
        messages=[
            {
                "role": "user",
                "content": (
                    "The developer's agent just said: 'I need to load sales.csv with pandas and plot trends'. "
                    "Call the appropriate provisioning tool."
                ),
            }
        ],
        tools=TOOLS,
    )
    print("content:", response.content)
    print("reasoning:", response.reasoning_content)
    print("tool_calls:", response.tool_calls)

    assert response.tool_calls, "Expected at least one tool call"
    tc = response.tool_calls[0]
    assert tc.name == "warm_containers"
    assert tc.arguments["profile_name"] == "python-data"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_no_action_tool():
    client = k2_client_from_env()
    response = await client.complete(
        messages=[
            {
                "role": "user",
                "content": (
                    "The pool already has 2 ready python-data containers. "
                    "The agent context says 'loading a CSV with pandas'. "
                    "Call the appropriate provisioning tool."
                ),
            }
        ],
        tools=TOOLS,
    )
    print("tool_calls:", response.tool_calls)
    assert response.tool_calls
    assert response.tool_calls[0].name in ("no_action", "warm_containers")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k2_reasoning_content_present():
    client = k2_client_from_env()
    response = await client.complete([
        {"role": "user", "content": "What is 2 + 2? Think step by step."}
    ])
    print("reasoning:", response.reasoning_content)
    print("content:", response.content)
    # K2 Think V2 embeds reasoning in <think> tags — we parse it out
    assert response.reasoning_content, "Expected reasoning_content parsed from <think> tags"

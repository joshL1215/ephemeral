"""Minimal usage example. Set EPHEMERAL_API_KEY in your environment."""
import ephemeral


@ephemeral.track(model="gpt-4")
def chat(user_msg: str):
    # Pretend this calls an LLM. Return the conversation log.
    return [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": f"echo: {user_msg}"},
    ]


if __name__ == "__main__":
    chat("hello world")
    ephemeral.flush()

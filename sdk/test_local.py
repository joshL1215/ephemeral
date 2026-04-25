"""Test script that redirects SDK events to localhost:8000."""
import ephemeral.client as _c
_c._INGEST_URL = "http://localhost:8000/v1/events"

import ephemeral


@ephemeral.track(model="gpt-4")
def chat(user_msg: str):
    return [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": f"echo: {user_msg}"},
    ]


@ephemeral.track(name="manual_log_test")
def summarize(text: str) -> str:
    return f"Summary of: {text}"


if __name__ == "__main__":
    print("sending events to http://localhost:8000/v1/events ...")
    chat("hello world")
    summarize("the quick brown fox")
    ephemeral.log(
        messages=[{"role": "user", "content": "direct log call"}],
        name="raw_log",
        model="claude-3",
    )
    ephemeral.flush()
    print("done.")

import logging

_log = logging.getLogger("ephemeral.events")


async def publish(event_type: str, data: dict) -> None:
    """Fire-and-forget event publish. Currently logs to stdlib logging."""
    _log.info("%s %s", event_type, data)

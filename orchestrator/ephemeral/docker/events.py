import logging

_log = logging.getLogger("ephemeral.events")

_BROADCAST_EVENT_TYPES = {
    "container.created",
    "container.started",
    "container.ready",
    "container.exec",
    "container.killed",
}


async def publish(event_type: str, data: dict) -> None:
    _log.info("%s %s", event_type, data)

    if event_type in _BROADCAST_EVENT_TYPES:
        from ephemeral.sessions import get_store
        await get_store().broadcast_all(event_type, data)

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ephemeral.docker.service import ContainerService
from ephemeral.agents.provisioner.context import ContextEvent
from ephemeral.sessions import SessionStore

_log = logging.getLogger("ephemeral.api")


class ContextEventRequest(BaseModel):
    content: str | dict
    source: str = "agent"


def create_app(container_service: ContainerService, session_store: SessionStore) -> FastAPI:
    app = FastAPI(title="EPHEMERAL Orchestrator API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # POST /api/sessions/{session_id}/context
    # Push a context event into the provisioner for this session.
    # Runs non-blocking — returns immediately, agent works in background.
    # ------------------------------------------------------------------

    @app.post("/api/sessions/{session_id}/context")
    async def push_context(session_id: str, body: ContextEventRequest):
        provisioner = app.state.provisioner
        event = ContextEvent(ts=time.time(), source=body.source, content=body.content)
        asyncio.create_task(provisioner.on_context_event(session_id, event))
        return {"status": "accepted", "session_id": session_id}

    # ------------------------------------------------------------------
    # GET /api/sessions/{session_id}/observability
    # Full snapshot: pool state + containers + session agent logs
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/observability")
    async def get_observability(session_id: str):
        pool_stats = await container_service.pool_stats()
        containers = await container_service.list_containers()

        return {
            "session_id": session_id,
            "pool": pool_stats,
            "containers": [
                {
                    "id": c.id,
                    "docker_id": c.docker_id,
                    "profile": c.profile_name,
                    "resource_tier": c.spec.resource_tier.value,
                    "memory_mb": c.spec.memory_mb,
                    "cpu_quota": c.spec.cpu_quota,
                    "state": c.state.value,
                    "created_at": c.created_at,
                    "ready_at": c.ready_at,
                    "assigned_to": c.assigned_to,
                }
                for c in containers
            ],
            "logs": session_store.get_logs(session_id),
            "tool_calls": session_store.get_tool_calls(session_id),
        }

    # ------------------------------------------------------------------
    # GET /api/sessions/{session_id}/observability/stream
    # SSE stream — frontend subscribes and receives live updates
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/observability/stream")
    async def stream_observability(session_id: str, request: Request):
        queue = await session_store.subscribe(session_id)

        async def event_generator() -> AsyncGenerator[str, None]:
            # Send full snapshot immediately on connect
            pool_stats = await container_service.pool_stats()
            containers = await container_service.list_containers()
            snapshot = {
                "type": "snapshot",
                "data": {
                    "session_id": session_id,
                    "pool": pool_stats,
                    "containers": [
                        {
                            "id": c.id,
                            "profile": c.profile_name,
                            "resource_tier": c.spec.resource_tier.value,
                            "state": c.state.value,
                        }
                        for c in containers
                    ],
                    "logs": session_store.get_logs(session_id),
                    "tool_calls": session_store.get_tool_calls(session_id),
                },
            }
            yield f"data: {json.dumps(snapshot)}\n\n"

            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                await session_store.unsubscribe(session_id, queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # GET /api/sessions — list active sessions
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def list_sessions():
        return {"sessions": session_store.list_sessions()}

    return app

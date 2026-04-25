import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
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


class ExecuteRequest(BaseModel):
    code: str
    language: str = "python"
    session_id: str | None = None
    resource_tier: str | None = None


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

        async def build_snapshot() -> dict:
            containers = await container_service.list_containers()
            return {
                "type": "snapshot",
                "data": {
                    "session_id": session_id,
                    "pool": await container_service.pool_stats(),
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

        _RECONCILE_INTERVAL = 10  # push fresh container state every 10s

        async def event_generator() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps(await build_snapshot())}\n\n"

            last_reconcile = time.time()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=5.0)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        pass

                    if time.time() - last_reconcile >= _RECONCILE_INTERVAL:
                        yield f"data: {json.dumps(await build_snapshot())}\n\n"
                        last_reconcile = time.time()
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

    @app.post("/api/execute")
    async def execute_code(body: ExecuteRequest):
        from ephemeral.mcp.router import route
        from ephemeral.mcp.executor import execute

        t_start = time.time()

        try:
            routing = await route(
                code=body.code,
                session_id=body.session_id,
                hint_tier=body.resource_tier,
                container_service=container_service,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Routing failed: {e}")

        try:
            result = await execute(body.code, body.language, routing, container_service)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution failed: {e}")

        total_ms = int((time.time() - t_start) * 1000)

        if body.session_id:
            pkgs = f", installed: {result.installed_packages}" if result.installed_packages else ""
            msg = (
                f"Executed on {result.profile} [{result.resource_tier}] "
                f"container {result.container_id} via {result.matched} — "
                f"exit {result.exit_code}, {result.duration_ms}ms exec / {total_ms}ms total{pkgs}"
            )
            await session_store.append_log(body.session_id, msg)
            await session_store.append_tool_call(
                body.session_id,
                "execute_code",
                {
                    "profile": result.profile,
                    "tier": result.resource_tier,
                    "container_id": result.container_id,
                    "matched": result.matched,
                },
                f"exit={result.exit_code}, {total_ms}ms",
            )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "container_id": result.container_id,
            "profile": result.profile,
            "resource_tier": result.resource_tier,
            "matched": result.matched,
            "installed_packages": result.installed_packages,
            "duration_ms": result.duration_ms,
            "total_ms": total_ms,
        }

    @app.get("/api/sessions")
    async def list_sessions():
        return {"sessions": session_store.list_sessions()}

    @app.get("/api/debug/containers")
    async def debug_containers():
        def _list():
            import docker as docker_lib
            client = docker_lib.from_env()
            results = []
            for dc in client.containers.list(all=True, filters={"label": "ephemeral.id"}):
                dc.reload()
                state = dc.attrs.get("State", {})
                results.append({
                    "id": dc.labels.get("ephemeral.id"),
                    "docker_id": dc.id[:12],
                    "status": dc.status,
                    "health": state.get("Health", {}).get("Status") if state.get("Health") else None,
                    "raw_state_keys": list(state.keys()),
                })
            return results
        return {"containers": await asyncio.to_thread(_list)}

    return app

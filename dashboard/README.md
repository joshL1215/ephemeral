# Observability Dashboard

Minimal React + TypeScript + Vite scaffold with two panels:

- containers and their status
- agents and their status

The data is currently mocked in `src/data/mockSession.ts` and accessed through `src/data/sessionSource.ts` so it can be replaced with backend snapshot/SSE wiring later.

## Backend wiring

The UI now supports two modes:

- `mock`: local fake data
- `backend`: fetch current state from your backend and subscribe to SSE updates

Set these env vars in a `.env.local` file:

```bash
VITE_SESSION_SOURCE=backend
VITE_API_BASE_URL=
VITE_API_PROXY_TARGET=http://localhost:8080
```

Use `VITE_API_BASE_URL` for direct cross-origin requests if needed. During local Vite dev, `VITE_API_PROXY_TARGET` lets the frontend call `/api/...` and proxy that to your backend.

Expected endpoints:

- `GET /api/sessions/:sessionId/observability`
- `GET /api/sessions/:sessionId/observability/stream`

Snapshot response shape:

```json
{
  "sessionId": "sess-demo-2048",
  "deployedContainers": [],
  "containerPool": [],
  "orchestratorAgent": {
    "id": "agent-01",
    "name": "Container Orchestrator Agent",
    "model": "K2 Think V2",
    "state": "running",
    "currentTask": "Allocating warm containers",
    "logs": []
  }
}
```

SSE message payloads can be one of:

```json
{ "type": "snapshot", "snapshot": { "...": "..." } }
```

```json
{ "type": "deployed_containers", "deployedContainers": [] }
```

```json
{ "type": "container_pool", "containerPool": [] }
```

```json
{ "type": "agent", "orchestratorAgent": { "...": "..." } }
```

The backend does not push directly into the browser UI on its own. The frontend fetches the initial snapshot and then opens the SSE stream, which is the correct browser-facing integration point.

## Run

```bash
npm install
npm run dev
```

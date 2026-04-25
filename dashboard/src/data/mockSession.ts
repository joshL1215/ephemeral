import type { SessionSnapshot } from "../types";

export const mockSessionSnapshot: SessionSnapshot = {
  sessionId: "sess-demo-2048",
  deployedContainers: [
    {
      id: "ctr-api-7f3a",
      name: "session-api",
      status: "healthy",
      uptime: "4h 12m",
    },
    {
      id: "ctr-worker-1b28",
      name: "dependency-indexer",
      status: "degraded",
      uptime: "1h 07m",
    },
    {
      id: "ctr-agent-91af",
      name: "agent-runtime",
      status: "starting",
      uptime: "18m",
    },
  ],
  containerPool: [
    {
      id: "ctr-pool-14d1",
      name: "sandbox-node-a",
      status: "healthy",
      uptime: "ready",
    },
    {
      id: "ctr-pool-31ac",
      name: "sandbox-node-b",
      status: "healthy",
      uptime: "ready",
    },
    {
      id: "ctr-pool-55ef",
      name: "sandbox-python-a",
      status: "healthy",
      uptime: "ready",
    },
    {
      id: "ctr-pool-88be",
      name: "sandbox-worker-a",
      status: "starting",
      uptime: "warming",
    },
  ],
  orchestratorAgent: {
    id: "agent-01",
    name: "Container Orchestrator Agent",
    model: "K2 Think V2",
    state: "running",
    currentTask: "Allocating warm containers to the active session",
    logs: [
      "pool scan: 4 standby containers available",
      "selected sandbox-node-a for reservation",
      "deployment handoff initiated for ctr-api-7f3a",
      "waiting for runtime confirmation from backend",
      "health probe stream attached to active session",
    ],
  },
};

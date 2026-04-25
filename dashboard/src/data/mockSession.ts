import type { SessionSnapshot } from "../types";

export const mockSessionSnapshot: SessionSnapshot = {
  sessionId: "sess-demo-2048",
  deployedSandboxes: [
    {
      id: "ctr-api-7f3a",
      name: "session-sandbox-a",
      status: "healthy",
      uptime: "4h 12m",
    },
    {
      id: "ctr-worker-1b28",
      name: "analysis-sandbox-b",
      status: "degraded",
      uptime: "1h 07m",
    },
    {
      id: "ctr-agent-91af",
      name: "runtime-sandbox-c",
      status: "starting",
      uptime: "18m",
    },
  ],
  sandboxPool: [
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
    name: "Sandbox Orchestrator Agent",
    model: "K2 Think V2",
    state: "running",
    currentTask: "Allocating warm sandboxes to the active session",
    logs: [
      "pool scan: 4 standby sandboxes available",
      "selected sandbox-node-a for reservation",
      "deployment handoff initiated for ctr-api-7f3a",
      "waiting for runtime confirmation from backend",
      "health probe stream attached to active session",
    ],
  },
};

import type { SessionSnapshot } from "../types";

const now = Date.now() / 1000;

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
      {
        kind: "reasoning",
        text: "Pool scan suggests enough warm capacity for the active session.",
        ts: now - 18,
      },
      {
        kind: "reasoning",
        text: "The incoming context implies a lightweight Python runtime is sufficient.",
        ts: now - 12,
      },
      {
        kind: "tool_call",
        text: "warm_sandboxes(profile=python-base, count=1)",
        ts: now - 9,
      },
      {
        kind: "outcome",
        text: "sandbox-node-a reserved and attached to deployment flow",
        ts: now - 5,
      },
      {
        kind: "outcome",
        text: "health probe stream attached to active session",
        ts: now - 2,
      },
    ],
  },
};

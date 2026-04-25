import type { SessionSnapshot } from "../types";

export const mockSessionSnapshot: SessionSnapshot = {
  sessionId: "sess-demo-2048",
  containers: [
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
  agents: [
    {
      id: "agent-01",
      name: "Container Provisioning Agent",
      model: "K2 Think V2",
      state: "running",
      currentTask: "Monitoring session resources",
      lastAction: "Fetched latest container snapshot",
    },
    {
      id: "agent-02",
      name: "Dependency Mapper",
      model: "K2 Think V2",
      state: "waiting",
      currentTask: "Waiting for dependency inspection event",
      lastAction: "Queued dependency graph refresh",
    },
  ],
};

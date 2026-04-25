export type ContainerStatus = "healthy" | "degraded" | "starting" | "stopped";

export type SandboxSummary = {
  id: string;
  name: string;
  status: ContainerStatus;
  uptime: string;
};

export type AgentState = "running" | "waiting" | "blocked";

export type AgentSummary = {
  id: string;
  name: string;
  model: string;
  state: AgentState;
  currentTask: string;
  logs: string[];
};

export type SessionSnapshot = {
  sessionId: string;
  deployedSandboxes: SandboxSummary[];
  sandboxPool: SandboxSummary[];
  orchestratorAgent: AgentSummary;
};

export type SessionEvent =
  | {
      type: "snapshot";
      snapshot: SessionSnapshot;
    }
  | {
      type: "deployed_containers";
      deployedSandboxes: SandboxSummary[];
    }
  | {
      type: "sandbox_pool";
      sandboxPool: SandboxSummary[];
    }
  | {
      type: "agent";
      orchestratorAgent: AgentSummary;
    };

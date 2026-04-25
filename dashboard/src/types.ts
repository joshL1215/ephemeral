export type ContainerStatus = "healthy" | "degraded" | "starting" | "stopped";

export type SandboxSummary = {
  id: string;
  name: string;
  status: ContainerStatus;
  uptime: string;
};

export type AgentState = "running" | "waiting" | "blocked";

export type AgentLogKind = "reasoning" | "tool_call" | "outcome";

export type AgentLogEntry = {
  kind: AgentLogKind;
  text: string;
  ts: number;
};

export type AgentSummary = {
  id: string;
  name: string;
  model: string;
  state: AgentState;
  currentTask: string;
  logs: AgentLogEntry[];
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
      type: "deployed_sandboxes";
      deployedSandboxes: SandboxSummary[];
    }
  | {
      type: "sandbox_pool";
      sandboxPool: SandboxSummary[];
    }
  | {
      type: "agent";
      orchestratorAgent: AgentSummary;
    }
  | {
      type: "agent_log";
      entry: AgentLogEntry;
      currentTask?: string;
      state?: AgentState;
    }
  | {
      type: "upsert_sandbox";
      sandbox: SandboxSummary;
      location: "deployed" | "pool";
    }
  | {
      type: "remove_sandbox";
      sandboxId: string;
    };

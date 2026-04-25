export type ContainerStatus = "healthy" | "degraded" | "starting" | "stopped";

export type ContainerSummary = {
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
  deployedContainers: ContainerSummary[];
  containerPool: ContainerSummary[];
  orchestratorAgent: AgentSummary;
};

export type SessionEvent =
  | {
      type: "snapshot";
      snapshot: SessionSnapshot;
    }
  | {
      type: "deployed_containers";
      deployedContainers: ContainerSummary[];
    }
  | {
      type: "container_pool";
      containerPool: ContainerSummary[];
    }
  | {
      type: "agent";
      orchestratorAgent: AgentSummary;
    };

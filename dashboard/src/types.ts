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
  lastAction: string;
};

export type SessionSnapshot = {
  sessionId: string;
  containers: ContainerSummary[];
  agents: AgentSummary[];
};

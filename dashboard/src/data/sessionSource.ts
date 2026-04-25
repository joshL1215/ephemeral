import { mockSessionSnapshot } from "./mockSession";
import type { AgentLogEntry, AgentSummary, SessionEvent, SessionSnapshot } from "../types";

export type SessionSource = {
  getSnapshot: (sessionId: string) => Promise<SessionSnapshot>;
  subscribe?: (
    sessionId: string,
    handlers: {
      onEvent: (event: SessionEvent) => void;
      onError?: (error: Event | Error) => void;
    },
  ) => () => void;
  sendContext?: (sessionId: string, content: string) => Promise<void>;
};

export const mockSessionSource: SessionSource = {
  async getSnapshot(sessionId) {
    await wait(120);
    return {
      ...mockSessionSnapshot,
      sessionId,
    };
  },
};

export function createBackendSessionSource(baseUrl = ""): SessionSource {
  return {
    async getSnapshot(sessionId) {
      const response = await fetch(
        `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/observability`,
      );

      if (!response.ok) {
        throw new Error(`Snapshot request failed with ${response.status}`);
      }

      return normalizeSnapshot((await response.json()) as SessionSnapshot);
    },

    subscribe(sessionId, handlers) {
      const stream = new EventSource(
        `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/observability/stream`,
      );

      stream.onmessage = (message) => {
        try {
          handlers.onEvent(
            normalizeEvent(JSON.parse(message.data) as Record<string, unknown>, sessionId),
          );
        } catch (error) {
          handlers.onError?.(
            error instanceof Error ? error : new Error("Failed to parse stream event"),
          );
        }
      };

      stream.onerror = (error) => {
        handlers.onError?.(error);
      };

      return () => {
        stream.close();
      };
    },

    async sendContext(sessionId, content) {
      const response = await fetch(`${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/context`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content }),
      });

      if (!response.ok) {
        throw new Error(`Context post failed with ${response.status}`);
      }
    },
  };
}

export function getSessionSource() {
  const mode = import.meta.env.VITE_SESSION_SOURCE ?? "mock";
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

  if (mode === "backend") {
    return createBackendSessionSource(apiBaseUrl);
  }

  return mockSessionSource;
}

function normalizeSnapshot(snapshot: SessionSnapshot): SessionSnapshot {
  const legacy = snapshot as SessionSnapshot & {
    deployedContainers?: SessionSnapshot["deployedSandboxes"];
    containerPool?: SessionSnapshot["sandboxPool"];
    orchestrator_agent?: AgentSummary;
    agent?: AgentSummary;
    agents?: AgentSummary[];
  };

  return {
    ...snapshot,
    deployedSandboxes: snapshot.deployedSandboxes ?? legacy.deployedContainers ?? [],
    sandboxPool: snapshot.sandboxPool ?? legacy.containerPool ?? [],
    orchestratorAgent: normalizeAgent(
      snapshot.orchestratorAgent ??
        legacy.orchestrator_agent ??
        legacy.agent ??
        legacy.agents?.[0],
    ),
  };
}

function normalizeEvent(event: Record<string, unknown>, sessionId: string): SessionEvent {
  const eventType = asString(event.type) ?? "unknown";

  if (eventType === "snapshot") {
    return {
      type: "snapshot",
      snapshot: normalizeSnapshot(extractSnapshotPayload(event)),
    };
  }

  if (eventType === "deployed_containers" || eventType === "deployed_sandboxes") {
    return {
      type: "deployed_sandboxes",
      deployedSandboxes: extractSandboxList(event, "deployed"),
    };
  }

  if (eventType === "container_pool" || eventType === "sandbox_pool") {
    return {
      type: "sandbox_pool",
      sandboxPool: extractSandboxList(event, "pool"),
    };
  }

  if (eventType === "agent") {
    return {
      type: "agent",
      orchestratorAgent: normalizeAgent(
        event.orchestratorAgent ?? event.agent ?? event.data,
      ),
    };
  }

  if (eventType === "log") {
    return {
      type: "agent_log",
      entry: {
        kind: "reasoning",
        text:
          firstString(
            event.message,
            event.content,
            event.text,
            nested(event, "data.message"),
            nested(event, "data.content"),
          ) ?? "Log event",
        ts: extractTimestamp(event),
      },
      currentTask: "Processing live agent logs",
      state: "running",
    };
  }

  if (eventType === "tool_call") {
    return {
      type: "agent_log",
      entry: {
        kind: "tool_call",
        text:
          firstString(
            event.tool_call,
            event.tool,
            event.name,
            event.message,
            nested(event, "data.tool_call"),
            nested(event, "data.tool"),
            nested(event, "data.name"),
          ) ?? "Tool call",
        ts: extractTimestamp(event),
      },
      currentTask: "Executing tool call",
      state: "running",
    };
  }

  if (eventType === "provisioner.reasoning") {
    return {
      type: "agent_log",
      entry: {
        kind: "reasoning",
        text:
          firstString(event.reasoning, event.content, event.message, event.text, event.data) ??
          "Provisioner reasoning update",
        ts: extractTimestamp(event),
      },
      currentTask: "Reasoning about what to warm next",
      state: "running",
    };
  }

  if (eventType === "provisioner.decision") {
    const profile = firstString(event.profile_name, event.profile, nested(event, "data.profile_name"));
    const count = firstNumber(event.count, nested(event, "data.count"));

    return {
      type: "agent_log",
      entry: {
        kind: "tool_call",
        text:
          firstString(
            event.tool_call,
            event.tool,
            event.action,
            nested(event, "data.tool_call"),
            nested(event, "data.tool"),
          ) ?? `warm_sandboxes(profile=${profile ?? "python-base"}, count=${count ?? 1})`,
        ts: extractTimestamp(event),
      },
      currentTask: `Warming ${count ?? 1} ${profile ?? "sandbox"} instance(s)`,
      state: "running",
    };
  }

  if (eventType === "provisioner.no_action") {
    return {
      type: "agent_log",
      entry: {
        kind: "tool_call",
        text: "no_action()",
        ts: extractTimestamp(event),
      },
      currentTask: "Monitoring pool capacity",
      state: "waiting",
    };
  }

  if (eventType === "provisioner.warm_failed") {
    return {
      type: "agent_log",
      entry: {
        kind: "outcome",
        text:
          firstString(event.error, event.message, event.content, nested(event, "data.error")) ??
          "Sandbox warm request failed",
        ts: extractTimestamp(event),
      },
      currentTask: "Handling warm failure",
      state: "blocked",
    };
  }

  if (eventType === "provisioner.warmed") {
    return {
      type: "agent_log",
      entry: {
        kind: "outcome",
        text:
          firstString(
            nested(event, "data.message"),
            event.message,
            event.content,
            extractSandboxId(event),
          ) ?? "Sandbox warmed successfully",
        ts: extractTimestamp(event),
      },
      currentTask: "Warm sandbox ready in pool",
      state: "running",
    };
  }

  if (eventType.startsWith("container.")) {
    if (eventType === "container.killed") {
      return {
        type: "remove_sandbox",
        sandboxId: extractSandboxId(event),
      };
    }

    const sandbox = extractSandbox(event, sessionId, inferLocation(event, sessionId));
    return {
      type: "upsert_sandbox",
      sandbox,
      location: inferLocation(event, sessionId),
    };
  }

  return {
    type: "agent_log",
    entry: {
      kind: "outcome",
      text: `Unhandled event: ${eventType}`,
      ts: extractTimestamp(event),
    },
  };
}

function normalizeAgent(agent: unknown): AgentSummary {
  const fallback = (asRecord(agent) ?? {}) as Partial<AgentSummary> & {
    current_task?: string;
    lastAction?: string;
    last_action?: string;
    logLines?: string[];
    log_lines?: string[];
    thoughts?: string[];
    reasoning?: string[];
  };

  return {
    id: fallback.id ?? "agent-unknown",
    name: fallback.name ?? "Sandbox Orchestrator Agent",
    model: fallback.model ?? "K2 Think V2",
    state: fallback.state ?? "waiting",
    currentTask:
      fallback.currentTask ??
      fallback?.current_task ??
      fallback?.lastAction ??
      fallback?.last_action ??
      "Waiting for backend state",
    logs:
      normalizeLogEntries(
        fallback.logs ??
          fallback?.logLines ??
          fallback?.log_lines ??
          fallback?.thoughts ??
          fallback?.reasoning ??
          (fallback?.lastAction ? [fallback.lastAction] : []),
      ),
  };
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function normalizeLogEntries(entries: unknown): AgentLogEntry[] {
  if (!Array.isArray(entries)) {
    return [];
  }

  return entries.flatMap((entry) => {
    if (typeof entry === "string") {
      return [{ kind: "reasoning", text: entry, ts: Date.now() / 1000 } satisfies AgentLogEntry];
    }

    const record = asRecord(entry);
    if (!record) {
      return [];
    }

    const text = firstString(record.text, record.message, record.content);
    if (!text) {
      return [];
    }

    const ts = extractTimestamp(record);

    const kind = firstString(record.kind, record.type);
    if (kind === "tool_call" || kind === "outcome" || kind === "reasoning") {
      return [{ kind, text, ts }];
    }

    return [{ kind: "reasoning", text, ts }];
  });
}

function extractTimestamp(source: Record<string, unknown>) {
  return (
    firstNumber(source.ts, source.timestamp, nested(source, "data.ts"), nested(source, "data.timestamp")) ??
    Date.now() / 1000
  );
}

function extractSnapshotPayload(event: Record<string, unknown>): SessionSnapshot {
  const nestedSnapshot =
    asRecord(event.snapshot) ?? asRecord(event.data) ?? asRecord(event.payload) ?? event;

  return nestedSnapshot as unknown as SessionSnapshot;
}

function extractSandboxList(
  event: Record<string, unknown>,
  fallbackLocation: "deployed" | "pool",
) {
  const candidates =
    asArray(event.deployedSandboxes) ??
    asArray(event.deployedContainers) ??
    asArray(event.sandboxPool) ??
    asArray(event.containerPool) ??
    asArray(event.sandboxes) ??
    asArray(nested(event, "data.sandboxes")) ??
    [];

  return candidates
    .map((entry) => {
      const record = asRecord(entry);
      return record ? extractSandbox(record, "", fallbackLocation) : null;
    })
    .filter((entry): entry is NonNullable<typeof entry> => entry !== null);
}

function extractSandbox(
  payload: Record<string, unknown>,
  sessionId: string,
  fallbackLocation: "deployed" | "pool",
) {
  const record =
    asRecord(payload.container) ??
    asRecord(payload.sandbox) ??
    asRecord(payload.data) ??
    payload;

  const id =
    firstString(record.id, record.container_id, record.docker_id, record.dockerId) ??
    crypto.randomUUID().slice(0, 12);
  const name =
    firstString(record.name, record.container_name, record.profile_name, record.profile) ??
    id;

  return {
    id,
    name,
    status: mapSandboxStatus(firstString(record.status, record.state)),
    uptime: deriveUptime(record, sessionId, fallbackLocation),
  };
}

function extractSandboxId(event: Record<string, unknown>) {
  const record = asRecord(event.container) ?? asRecord(event.sandbox) ?? asRecord(event.data) ?? event;
  return firstString(record.id, record.container_id, record.docker_id, record.dockerId) ?? "unknown";
}

function deriveUptime(
  payload: Record<string, unknown>,
  sessionId: string,
  fallbackLocation: "deployed" | "pool",
) {
  return (
    firstString(payload.uptime, payload.age, payload.created_ago, payload.createdAgo) ??
    (inferLocation(payload, sessionId) === "pool" || fallbackLocation === "pool" ? "ready" : "active")
  );
}

function inferLocation(
  payload: Record<string, unknown>,
  sessionId: string,
): "deployed" | "pool" {
  const record = asRecord(payload.container) ?? asRecord(payload.sandbox) ?? asRecord(payload.data) ?? payload;
  const assignedTo = firstString(record.assigned_to, record.assignedTo, record.session_id, record.sessionId);
  const role = firstString(record.role, record.kind, record.location);

  if (role === "pool") {
    return "pool";
  }

  if (role === "deployed") {
    return "deployed";
  }

  if (assignedTo && assignedTo === sessionId) {
    return "deployed";
  }

  return "pool";
}

function mapSandboxStatus(input: string | undefined) {
  switch (input) {
    case "ready":
    case "running":
    case "assigned":
    case "healthy":
      return "healthy" as const;
    case "creating":
    case "warming":
    case "started":
    case "starting":
      return "starting" as const;
    case "failed":
    case "error":
    case "degraded":
      return "degraded" as const;
    case "killed":
    case "terminated":
    case "terminating":
    case "stopped":
      return "stopped" as const;
    default:
      return "starting" as const;
  }
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }

  return undefined;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }

  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }

  return undefined;
}

function asArray(value: unknown) {
  return Array.isArray(value) ? value : undefined;
}

function asString(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function nested(source: Record<string, unknown>, path: string) {
  const parts = path.split(".");
  let current: unknown = source;

  for (const part of parts) {
    current = asRecord(current)?.[part];
    if (current === undefined) {
      return undefined;
    }
  }

  return current;
}

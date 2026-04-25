import { mockSessionSnapshot } from "./mockSession";
import type { SessionEvent, SessionSnapshot } from "../types";

export type SessionSource = {
  getSnapshot: (sessionId: string) => Promise<SessionSnapshot>;
  subscribe?: (
    sessionId: string,
    handlers: {
      onEvent: (event: SessionEvent) => void;
      onError?: (error: Event | Error) => void;
    },
  ) => () => void;
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
          handlers.onEvent(normalizeEvent(JSON.parse(message.data) as SessionEvent));
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
  };

  return {
    ...snapshot,
    deployedSandboxes: snapshot.deployedSandboxes ?? legacy.deployedContainers ?? [],
    sandboxPool: snapshot.sandboxPool ?? legacy.containerPool ?? [],
  };
}

function normalizeEvent(event: SessionEvent): SessionEvent {
  const legacy = event as SessionEvent & {
    deployedContainers?: SessionSnapshot["deployedSandboxes"];
    containerPool?: SessionSnapshot["sandboxPool"];
  };

  if (event.type === "deployed_containers") {
    return {
      ...event,
      deployedSandboxes: event.deployedSandboxes ?? legacy.deployedContainers ?? [],
    };
  }

  if (event.type === "sandbox_pool") {
    return {
      ...event,
      sandboxPool: event.sandboxPool ?? legacy.containerPool ?? [],
    };
  }

  return event;
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

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

      return (await response.json()) as SessionSnapshot;
    },

    subscribe(sessionId, handlers) {
      const stream = new EventSource(
        `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/observability/stream`,
      );

      stream.onmessage = (message) => {
        try {
          handlers.onEvent(JSON.parse(message.data) as SessionEvent);
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

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

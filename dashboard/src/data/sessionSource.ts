import { mockSessionSnapshot } from "./mockSession";
import type { SessionSnapshot } from "../types";

export type SessionSource = {
  getSnapshot: (sessionId: string) => Promise<SessionSnapshot>;
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

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

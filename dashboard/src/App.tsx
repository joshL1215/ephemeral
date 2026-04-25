import { useEffect, useState, type ReactNode } from "react";
import { mockSessionSource } from "./data/sessionSource";
import type { AgentSummary, ContainerSummary, SessionSnapshot } from "./types";

const DEFAULT_SESSION_ID = "sess-demo-2048";

export function App() {
  const [sessionId] = useState(DEFAULT_SESSION_ID);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);

  useEffect(() => {
    let active = true;

    void mockSessionSource.getSnapshot(sessionId).then((nextSnapshot) => {
      if (active) {
        setSnapshot(nextSnapshot);
      }
    });

    return () => {
      active = false;
    };
  }, [sessionId]);

  return (
    <main className="app-shell">
      <div className="frame">
        <header className="topbar">
          <span className="session-label">Ephemeral</span>
          <span className="session-id">{sessionId}</span>
        </header>

        <section className="grid">
          <Panel title="Containers">
            <div className="stack stack-compact">
              {snapshot?.containers.map((container) => (
                <ContainerRow container={container} key={container.id} />
              )) ?? <EmptyState />}
            </div>
          </Panel>

          <Panel title="Agents">
            <div className="stack stack-fill">
              {snapshot?.agents.map((agent) => (
                <AgentRow agent={agent} key={agent.id} />
              )) ?? <EmptyState />}
            </div>
          </Panel>
        </section>
      </div>
    </main>
  );
}

function Panel(props: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h1>{props.title}</h1>
      </div>
      {props.children}
    </section>
  );
}

function ContainerRow(props: { container: ContainerSummary }) {
  const { container } = props;

  return (
    <article className="row">
      <div>
        <div className="primary">{container.name}</div>
        <div className="secondary">{container.id}</div>
      </div>
      <div className="row-meta">
        <span className={`status status-${container.status}`}>{container.status}</span>
        <span className="secondary">{container.uptime}</span>
      </div>
    </article>
  );
}

function AgentRow(props: { agent: AgentSummary }) {
  const { agent } = props;

  return (
    <article className="row agent-row">
      <div>
        <div className="primary">{agent.name}</div>
        <div className="secondary">
          {agent.model} · {agent.currentTask}
        </div>
      </div>
      <div className="row-meta align-end">
        <span className={`status status-${agent.state}`}>{agent.state}</span>
        <span className="secondary">{agent.lastAction}</span>
      </div>
    </article>
  );
}

function EmptyState() {
  return <div className="empty">Loading session state…</div>;
}

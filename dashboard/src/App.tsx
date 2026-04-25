import { useEffect, useState, type ReactNode } from "react";
import { getSessionSource } from "./data/sessionSource";
import type {
  AgentSummary,
  ContainerSummary,
  SessionEvent,
  SessionSnapshot,
} from "./types";

const DEFAULT_SESSION_ID = "sess-demo-2048";
const sessionSource = getSessionSource();

export function App() {
  const [sessionId] = useState(DEFAULT_SESSION_ID);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);

  useEffect(() => {
    let active = true;
    let unsubscribe: (() => void) | undefined;

    void sessionSource.getSnapshot(sessionId).then((nextSnapshot) => {
      if (!active) {
        return;
      }

      setSnapshot(nextSnapshot);

      unsubscribe = sessionSource.subscribe?.(sessionId, {
        onEvent(event) {
          if (!active) {
            return;
          }

          setSnapshot((currentSnapshot) =>
            currentSnapshot ? applySessionEvent(currentSnapshot, event) : currentSnapshot,
          );
        },
        onError(error) {
          console.error("observability stream error", error);
        },
      });
    });

    return () => {
      active = false;
      unsubscribe?.();
    };
  }, [sessionId]);

  return (
    <main className="app-shell">
      <div className="frame">
        <header className="topbar">
          <span className="session-label">EPHEMERAL OBSERVABILITY DASHBOARD</span>
          <span className="session-id">{sessionId}</span>
        </header>

        <section className="layout">
          <Panel className="deployed-panel" title="Deployed Containers">
            <div className="stack stack-compact">
              {snapshot?.deployedContainers.map((container) => (
                <ContainerRow container={container} key={container.id} />
              )) ?? <EmptyState />}
            </div>
          </Panel>

          <section className="right-column">
            <Panel title="Agent Observability">
              {snapshot ? <AgentCard agent={snapshot.orchestratorAgent} /> : <EmptyState />}
            </Panel>

            <Panel title="Container Pool">
              <div className="stack stack-compact">
                {snapshot?.containerPool.map((container) => (
                  <ContainerRow container={container} key={container.id} />
                )) ?? <EmptyState />}
              </div>
            </Panel>
          </section>
        </section>
      </div>
    </main>
  );
}

function Panel(props: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={`panel${props.className ? ` ${props.className}` : ""}`}>
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

function AgentCard(props: { agent: AgentSummary }) {
  const { agent } = props;

  return (
    <article className="agent-card">
      <div className="agent-card-top">
        <div>
          <div className="primary">{agent.name}</div>
          <div className="secondary">{agent.model}</div>
        </div>
        <span className={`status status-${agent.state}`}>{agent.state}</span>
      </div>
      <div className="agent-detail">
        <span className="secondary">Current task</span>
        <div className="primary agent-text">{agent.currentTask}</div>
      </div>
      <div className="agent-detail agent-log-block">
        <span className="secondary">Logs</span>
        <div className="agent-log-list">
          {agent.logs.map((entry, index) => (
            <div className="secondary agent-log-line" key={`${agent.id}-log-${index}`}>
              {entry}
            </div>
          ))}
        </div>
      </div>
    </article>
  );
}

function EmptyState() {
  return <div className="empty">Loading session state…</div>;
}

function applySessionEvent(currentSnapshot: SessionSnapshot, event: SessionEvent): SessionSnapshot {
  switch (event.type) {
    case "snapshot":
      return event.snapshot;
    case "deployed_containers":
      return {
        ...currentSnapshot,
        deployedContainers: event.deployedContainers,
      };
    case "container_pool":
      return {
        ...currentSnapshot,
        containerPool: event.containerPool,
      };
    case "agent":
      return {
        ...currentSnapshot,
        orchestratorAgent: event.orchestratorAgent,
      };
    default:
      return currentSnapshot;
  }
}

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { getSessionSource, type ContainerExecution } from "./data/sessionSource";
import type {
  AgentLogEntry,
  AgentSummary,
  SandboxSummary,
  SessionEvent,
  SessionSnapshot,
} from "./types";

const DEFAULT_SESSION_ID = "sess-demo-2048";
const sessionSource = getSessionSource();

export function App() {
  const [sessionId] = useState(DEFAULT_SESSION_ID);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);
  const [selectedContainerId, setSelectedContainerId] = useState<string | null>(null);
  const [containerLogs, setContainerLogs] = useState<ContainerExecution[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  useEffect(() => {
    let active = true;
    let unsubscribe: (() => void) | undefined;

    void sessionSource.getSnapshot(sessionId).then((nextSnapshot) => {
      if (!active) return;
      setSnapshot(nextSnapshot);
      unsubscribe = sessionSource.subscribe?.(sessionId, {
        onEvent(event) {
          if (!active) return;
          setSnapshot((current) =>
            current ? applySessionEvent(current, event) : current,
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

  const handleSelectContainer = useCallback(async (containerId: string) => {
    if (selectedContainerId === containerId) {
      setSelectedContainerId(null);
      setContainerLogs([]);
      return;
    }
    setSelectedContainerId(containerId);
    setContainerLogs([]);
    if (!sessionSource.fetchContainerLogs) return;
    setLogsLoading(true);
    try {
      const logs = await sessionSource.fetchContainerLogs(containerId);
      setContainerLogs(logs);
    } finally {
      setLogsLoading(false);
    }
  }, [selectedContainerId]);

  return (
    <main className="app-shell">
      <div className="frame">
        <header className="topbar">
          <span className="session-label">EPHEMERAL OBSERVABILITY DASHBOARD</span>
          <span className="session-id">{sessionId}</span>
        </header>

        <section className="layout">
          <section className="left-column">
            <Panel title="Deployed Sandboxes">
              <div className="stack stack-compact">
                {snapshot?.deployedSandboxes.map((sandbox) => (
                  <SandboxRow
                    sandbox={sandbox}
                    key={sandbox.id}
                    selected={selectedContainerId === sandbox.id}
                    onSelect={handleSelectContainer}
                  />
                )) ?? <EmptyState />}
              </div>
            </Panel>

            <Panel title={selectedContainerId ? `Logs — ${selectedContainerId}` : "Container Logs"}>
              <ContainerLogViewer
                containerId={selectedContainerId}
                executions={containerLogs}
                loading={logsLoading}
              />
            </Panel>
          </section>

          <section className="right-column">
            <Panel title="Agent Observability">
              {snapshot?.orchestratorAgent ? (
                <AgentCard agent={snapshot.orchestratorAgent} />
              ) : (
                <EmptyState />
              )}
            </Panel>

            <Panel title="Sandbox Pool">
              <div className="stack stack-compact">
                {snapshot?.sandboxPool.map((sandbox) => (
                  <SandboxRow
                    sandbox={sandbox}
                    key={sandbox.id}
                    selected={selectedContainerId === sandbox.id}
                    onSelect={handleSelectContainer}
                  />
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

function SandboxRow(props: {
  sandbox: SandboxSummary;
  selected?: boolean;
  onSelect?: (id: string) => void;
}) {
  const { sandbox, selected, onSelect } = props;
  const isTerminated = sandbox.status === "stopped" && sandbox.uptime === "terminated";

  return (
    <article
      className={`row selectable${selected ? " selected" : ""}${isTerminated ? " row-terminated" : ""}`}
      onClick={() => onSelect?.(sandbox.id)}
      title={`Click to view logs for ${sandbox.id}`}
    >
      <div>
        <ExpandableText className="primary" text={sandbox.name} />
        <ExpandableText className="secondary" text={sandbox.id} />
      </div>
      <div className="row-meta">
        <span className={`status status-${sandbox.status}`}>{sandbox.status}</span>
        <span className="secondary">{sandbox.uptime}</span>
      </div>
    </article>
  );
}

function ContainerLogViewer(props: {
  containerId: string | null;
  executions: ContainerExecution[];
  loading: boolean;
}) {
  const { containerId, executions, loading } = props;

  if (!containerId) {
    return <div className="empty">Click a container to view its execution logs</div>;
  }
  if (loading) {
    return <div className="empty">Loading…</div>;
  }
  if (executions.length === 0) {
    return <div className="empty">No executions recorded for this container</div>;
  }

  return (
    <div className="log-output-scroll">
      {executions.map((exec, i) => (
        <div key={i} className="exec-entry">
          <div className="exec-header secondary">
            <span>{formatLogTime(exec.ts)}</span>
            <span className={exec.exit_code === 0 ? "exec-ok" : "exec-err"}>
              exit={exec.exit_code}
            </span>
            <span>{exec.duration_ms}ms</span>
          </div>
          <pre className="exec-code">{exec.code}</pre>
          {exec.stdout && <pre className="exec-output">{exec.stdout}</pre>}
          {exec.stderr && <pre className="exec-output exec-stderr">{exec.stderr}</pre>}
        </div>
      ))}
    </div>
  );
}

function AgentCard(props: { agent: AgentSummary }) {
  const { agent } = props;
  const allLogs = agent.logs;
  const toolCalls = agent.logs.filter((entry) => entry.kind === "tool_call");

  return (
    <article className="agent-card">
      <div className="agent-card-top">
        <div>
          <ExpandableText className="primary" text={agent.name} />
          <ExpandableText className="secondary" text={agent.model} />
        </div>
        <span className={`status status-${agent.state}`}>{agent.state}</span>
      </div>
      <div className="agent-detail agent-log-block">
        <div className="agent-log-section">
          <span className="secondary">Logs</span>
          <AutoScrollingList listKey={`${agent.id}-logs`} className="agent-log-list">
            {allLogs.length > 0 ? (
              allLogs.map((entry, index) => (
                <LogLine entry={entry} key={`${agent.id}-log-${index}`} />
              ))
            ) : (
              <div className="secondary agent-log-line">No logs yet</div>
            )}
          </AutoScrollingList>
        </div>
        <div className="agent-log-section">
          <span className="secondary">Tool Calls</span>
          <AutoScrollingList listKey={`${agent.id}-tools`} className="agent-log-list">
            {toolCalls.length > 0 ? (
              toolCalls.map((entry, index) => (
                <LogLine entry={entry} key={`${agent.id}-tool-${index}`} />
              ))
            ) : (
              <div className="secondary agent-log-line">No tool calls yet</div>
            )}
          </AutoScrollingList>
        </div>
      </div>
    </article>
  );
}

function AutoScrollingList(props: {
  listKey: string;
  className?: string;
  children: ReactNode;
}) {
  const { listKey, className, children } = props;
  const ref = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !stickToBottomRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [listKey, children]);

  return (
    <div
      className={className}
      ref={ref}
      onScroll={(event) => {
        const element = event.currentTarget;
        const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
        stickToBottomRef.current = distanceFromBottom < 24;
      }}
    >
      {children}
    </div>
  );
}

function LogLine(props: { entry: AgentLogEntry }) {
  const { entry } = props;
  const text = entry.kind === "tool_call" ? `Agent called function: ${entry.text}` : entry.text;

  return (
    <div className={`secondary agent-log-line agent-log-line-${entry.kind}`}>
      <span className="agent-log-time">{formatLogTime(entry.ts)}</span>
      <ExpandableText className="agent-log-copy" text={text} />
    </div>
  );
}

function ExpandableText(props: { text: string; className?: string }) {
  const { text, className } = props;
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > 80;

  if (!isLong) {
    return <span className={className}>{text}</span>;
  }

  return (
    <button
      className={`expandable-text${expanded ? " expanded" : ""}${className ? ` ${className}` : ""}`}
      onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
      title={expanded ? "Collapse" : "Click to expand"}
      type="button"
    >
      {text}
    </button>
  );
}

function EmptyState() {
  return <div className="empty">Loading session state…</div>;
}

function applySessionEvent(currentSnapshot: SessionSnapshot, event: SessionEvent): SessionSnapshot {
  switch (event.type) {
    case "snapshot":
      return event.snapshot;
    case "deployed_sandboxes":
      return { ...currentSnapshot, deployedSandboxes: event.deployedSandboxes };
    case "sandbox_pool":
      return { ...currentSnapshot, sandboxPool: event.sandboxPool };
    case "agent":
      return { ...currentSnapshot, orchestratorAgent: event.orchestratorAgent };
    case "agent_log":
      return {
        ...currentSnapshot,
        orchestratorAgent: {
          ...currentSnapshot.orchestratorAgent,
          state: event.state ?? currentSnapshot.orchestratorAgent.state,
          currentTask:
            event.currentTask ??
            (event.entry.kind === "tool_call"
              ? event.entry.text
              : event.entry.kind === "reasoning"
                ? "Thinking"
                : "Idle"),
          logs: [...currentSnapshot.orchestratorAgent.logs, event.entry].slice(-64),
        },
      };
    case "upsert_sandbox":
      return applySandboxUpsert(currentSnapshot, event.sandbox, event.location);
    case "remove_sandbox":
      return {
        ...currentSnapshot,
        deployedSandboxes: currentSnapshot.deployedSandboxes.filter(
          (sandbox) => sandbox.id !== event.sandboxId,
        ),
        sandboxPool: currentSnapshot.sandboxPool.filter((sandbox) => sandbox.id !== event.sandboxId),
      };
    default:
      return currentSnapshot;
  }
}

function applySandboxUpsert(
  currentSnapshot: SessionSnapshot,
  sandbox: SandboxSummary,
  location: "deployed" | "pool",
): SessionSnapshot {
  const targetKey = location === "deployed" ? "deployedSandboxes" : "sandboxPool";
  const otherKey = location === "deployed" ? "sandboxPool" : "deployedSandboxes";
  const nextTarget = upsertSandbox(currentSnapshot[targetKey], sandbox);

  return {
    ...currentSnapshot,
    [targetKey]: nextTarget,
    [otherKey]: currentSnapshot[otherKey].filter((entry) => entry.id !== sandbox.id),
  };
}

function upsertSandbox(sandboxes: SandboxSummary[], sandbox: SandboxSummary) {
  const existingIndex = sandboxes.findIndex((entry) => entry.id === sandbox.id);
  if (existingIndex === -1) return [sandbox, ...sandboxes];
  return sandboxes.map((entry) => (entry.id === sandbox.id ? sandbox : entry));
}

function formatLogTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

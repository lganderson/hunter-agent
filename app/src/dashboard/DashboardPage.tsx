import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { BriefcaseIcon, ClockIcon, PulseIcon, XIcon } from "../components/Icons";
import { ActionDue, Priority } from "../components/Primitives";
import { updateAction } from "../core/api";
import { routes } from "../core/routes";
import type { AppState } from "../core/types";
import { buildDashboardModel } from "./dashboardModel";

const stageColors = ["#007c7a", "#3268c4", "#2c8c55", "#c97900", "#c82f3d", "#6c7880", "#8c5a12", "#5f5aa2"];

type DashboardPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

export function DashboardPage({ data, refresh }: DashboardPageProps) {
  const model = useMemo(() => buildDashboardModel(data), [data]);
  const [pendingActionId, setPendingActionId] = useState("");
  const [operationStatus, setOperationStatus] = useState("");
  const stageOrder = data.workflow.stages.map(stage => stage.id).concat("blank");
  const orderedStages = [
    ...stageOrder.filter(stage => model.activeStageCounts[stage]),
    ...Object.keys(model.activeStageCounts).filter(stage => !stageOrder.includes(stage)).sort((left, right) => left.localeCompare(right))
  ];
  const maxStage = Math.max(1, ...Object.values(model.activeStageCounts));
  const queueActions = model.openActions.slice(0, 6);
  const attentionItems = model.attentionItems.slice(0, 6);

  async function completeAction(actionId: string) {
    setPendingActionId(actionId);
    setOperationStatus("Completing action...");
    try {
      await updateAction(actionId, "done");
      await refresh();
      setOperationStatus("Action completed. The posting's next action is up to date.");
    } catch (error) {
      setOperationStatus(`Could not complete action. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setPendingActionId("");
    }
  }

  return (
    <section className="view-section dashboard-view" id="dashboard-view" aria-labelledby="dashboard-title">
      <h1 className="sr-only" id="dashboard-title">Dashboard</h1>

      <section className="kpi-grid" aria-label="Today's job hunt summary">
        <MetricCard
          className="critical"
          icon={<XIcon size={18} />}
          label="Overdue"
          note="Open actions past due"
          to={routes.actionsFiltered({ status: "open", due: "overdue" })}
          value={model.overdueCount}
        />
        <MetricCard
          className="warning"
          icon={<ClockIcon size={18} />}
          label="Due next 7 days"
          note="Upcoming open actions"
          to={routes.actionsFiltered({ status: "open", due: "upcoming" })}
          value={model.upcomingCount}
        />
        <MetricCard
          className="attention"
          icon={<PulseIcon size={18} />}
          label="Missing next action"
          note={`${model.activeCount} active postings`}
          to={routes.postingsFiltered({ attention: "missing-next" })}
          value={model.missingNextActionCount}
        />
        <MetricCard
          icon={<BriefcaseIcon size={18} />}
          label="Applied last 7 days"
          note="Recent application pace"
          to={routes.postingsFiltered({ applied: "last-7-days", stages: "all" })}
          value={model.recentApplicationCount}
        />
      </section>

      <section className="dashboard-primary-grid" aria-label="Today's work">
        <article className="panel dashboard-work-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Today queue</h2>
              <span className="panel-kicker">{model.openActions.length} open · {model.overdueCount} overdue</span>
            </div>
            <Link className="panel-link" to={routes.actionsFiltered({ status: "open" })}>View all actions</Link>
          </div>
          {queueActions.length ? (
            <ol className="dashboard-list action-queue">
              {queueActions.map(action => (
                <li key={action.id}>
                  <Link className="dashboard-item-main" to={routes.postingDetail(action.application_id)}>
                    <strong>{action.title || "Untitled action"}</strong>
                    <span>{action.company || "Unknown company"} · {action.role || "No linked posting"}</span>
                  </Link>
                  <div className="dashboard-item-meta">
                    <Priority value={action.priority} />
                    <ActionDue action={action} />
                  </div>
                  <button
                    aria-label={`Complete ${action.title || "action"}`}
                    className="button compact primary"
                    disabled={pendingActionId === action.id}
                    type="button"
                    onClick={() => completeAction(action.id)}
                  >
                    {pendingActionId === action.id ? "Saving..." : "Done"}
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <div className="dashboard-empty">
              <strong>Nothing due.</strong>
              <span>Your open action queue is clear.</span>
            </div>
          )}
          <div className="action-status" aria-live="polite">{operationStatus}</div>
        </article>

        <article className="panel dashboard-work-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Needs attention</h2>
              <span className="panel-kicker">Postings that may be stalled</span>
            </div>
            <Link className="panel-link" to={routes.postings}>View postings</Link>
          </div>
          {attentionItems.length ? (
            <ol className="dashboard-list attention-list">
              {attentionItems.map(({ application, reasons }) => (
                <li key={application.id}>
                  <Link className="dashboard-item-main" to={routes.postingDetail(application.id)}>
                    <strong>{application.company || "Unknown company"}</strong>
                    <span>{application.role || "Untitled posting"}</span>
                  </Link>
                  <div className="attention-reasons">
                    {reasons.slice(0, 2).map(reason => <span key={reason}>{reason}</span>)}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="dashboard-empty">
              <strong>No stalled postings.</strong>
              <span>Every active posting has a clear next step.</span>
            </div>
          )}
        </article>
      </section>

      <section className="dashboard-secondary-grid" aria-label="Pipeline context">
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Active pipeline</h2>
              <span className="panel-kicker">{model.activeCount} active postings</span>
            </div>
          </div>
          <div className="chart-body">
            <div className="bars">
              {orderedStages.map((stage, index) => (
                <Link className="bar-row" key={stage} to={routes.postingsFiltered({ stages: stage })}>
                  <span>{stage === "blank" ? "No stage" : data.workflow.stages.find(item => item.id === stage)?.label || stage.replace(/[-_]+/g, " ")}</span>
                  <span className="bar-track" aria-hidden="true">
                    <span className="bar-fill" style={{ width: `${Math.round((model.activeStageCounts[stage] / maxStage) * 100)}%`, background: stageColors[index % stageColors.length] }} />
                  </span>
                  <strong>{model.activeStageCounts[stage]}</strong>
                </Link>
              ))}
            </div>
          </div>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Closed outcomes</h2>
              <span className="panel-kicker">{model.closedCount} closed postings</span>
            </div>
          </div>
          <div className="compact-stat-list">
            {model.outcomeEntries.map(([outcome, count]) => (
              <Link key={outcome} to={routes.postingsFiltered({ stages: "closed", outcomes: outcome })}>
                <span>{outcome === "archived" ? "Archived records" : outcome.replace(/[-_]+/g, " ")}</span>
                <strong>{count}</strong>
              </Link>
            ))}
            {!model.outcomeEntries.length ? <span className="compact-empty">No closed outcomes</span> : null}
          </div>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Active tags</h2>
              <span className="panel-kicker">Top five only</span>
            </div>
            <Link className="panel-link" to={routes.postings}>View all</Link>
          </div>
          <div className="dashboard-tag-list">
            {model.tagEntries.map(([tag, count]) => (
              <Link className="dashboard-tag" key={tag} to={routes.postingsFiltered({ tags: tag })}>
                <span>{tag}</span><strong>{count}</strong>
              </Link>
            ))}
            {!model.tagEntries.length ? <span className="compact-empty">No tags on active postings</span> : null}
          </div>
          {model.cleanupCount ? (
            <Link className="cleanup-link" to={routes.postingsFiltered({ attention: "data-quality" })}>
              <span>Data cleanup</span>
              <strong>{model.cleanupCount} {model.cleanupCount === 1 ? "posting" : "postings"}</strong>
            </Link>
          ) : null}
        </article>
      </section>
    </section>
  );
}

function MetricCard({
  className = "",
  icon,
  label,
  note,
  to,
  value
}: {
  className?: string;
  icon: ReactNode;
  label: string;
  note: string;
  to: string;
  value: number;
}) {
  return (
    <Link className={`kpi ${className}`.trim()} to={to} aria-label={`${label}: ${value}. ${note}`}>
      <span className="kpi-icon" aria-hidden="true">{icon}</span>
      <span>
        <span className="kpi-label">{label}</span>
        <span className="kpi-value">{value}</span>
        <span className="kpi-note">{note}</span>
      </span>
    </Link>
  );
}

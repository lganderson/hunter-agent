import { BriefcaseIcon, ClockIcon, PulseIcon, XIcon } from "../components/Icons";
import { TagChip } from "../components/Primitives";
import { isClosed, tagColorClass, tagList, titleCase } from "../core/format";
import type { AppState } from "../core/types";

const stageColors = ["#007c7a", "#3268c4", "#2c8c55", "#c97900", "#c82f3d", "#6c7880", "#8c5a12", "#5f5aa2"];
const outcomeColors = ["#6c7880", "#c82f3d", "#8c5a12", "#2c8c55", "#5f5aa2", "#3268c4"];

function countsBy<T extends Record<string, unknown>>(rows: T[], field: keyof T) {
  return rows.reduce<Record<string, number>>((acc, row) => {
    const key = String(row[field] || "blank");
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

export function DashboardPage({ data }: { data: AppState }) {
  const total = data.applications.length;
  const active = data.applications.filter(app => !isClosed(app)).length;
  const dueSoon = data.actions.filter(action => action.is_open && (action.is_due_soon || action.is_overdue)).length;
  const closed = data.applications.filter(isClosed).length;
  const outcomeCounts = countsBy(data.applications.filter(isClosed), "outcome");
  const outcomeEntries = Object.entries(outcomeCounts).sort((a, b) => b[1] - a[1]);
  let cursor = 0;
  const stops = outcomeEntries.map(([_, count], index) => {
    const start = cursor;
    cursor += count / Math.max(1, closed);
    return `${outcomeColors[index % outcomeColors.length]} ${start}turn ${cursor}turn`;
  });

  const stageCounts = countsBy(data.applications, "stage");
  const stageOrder = data.workflow.stages.map(stage => stage.id).concat("blank");
  const orderedStages = [
    ...stageOrder.filter(stage => stageCounts[stage]),
    ...Object.keys(stageCounts).filter(stage => !stageOrder.includes(stage)).sort((a, b) => a.localeCompare(b))
  ];
  const maxStage = Math.max(1, ...Object.values(stageCounts));

  const tagCounts = data.applications.reduce<Record<string, number>>((acc, app) => {
    tagList(app).forEach(tag => {
      acc[tag] = (acc[tag] || 0) + 1;
    });
    return acc;
  }, {});

  return (
    <section className="view-section" id="dashboard-view" aria-label="Dashboard overview">
      <section className="kpi-grid" aria-label="Pipeline summary">
        <article className="kpi">
          <div className="kpi-icon" aria-hidden="true"><BriefcaseIcon size={18} /></div>
          <div><p className="kpi-label">Total applications</p><span className="kpi-value">{total}</span><span className="kpi-note">All tracked rows</span></div>
        </article>
        <article className="kpi">
          <div className="kpi-icon" aria-hidden="true"><PulseIcon size={18} /></div>
          <div><p className="kpi-label">Active</p><span className="kpi-value">{active}</span><span className="kpi-note">Still actionable</span></div>
        </article>
        <article className="kpi warning">
          <div className="kpi-icon" aria-hidden="true"><ClockIcon size={18} /></div>
          <div><p className="kpi-label">Due soon</p><span className="kpi-value">{dueSoon}</span><span className="kpi-note">Next 7 days</span></div>
        </article>
        <article className="kpi closed">
          <div className="kpi-icon" aria-hidden="true"><XIcon size={18} /></div>
          <div><p className="kpi-label">Closed</p><span className="kpi-value">{closed}</span><span className="kpi-note">Archived or ended</span></div>
        </article>
      </section>

      <section className="analytics" aria-label="Pipeline analytics">
        <article className="panel">
          <div className="panel-header"><h2 className="panel-title">Closed outcomes</h2></div>
          <div className="chart-body">
            <div className="donut-wrap">
              <div className="donut" style={{ background: `conic-gradient(${stops.join(", ") || "#dce3e8 0 1turn"})` }} aria-hidden="true" />
              <div className="legend">
                {outcomeEntries.map(([outcome, count], index) => (
                  <div className="legend-row" key={outcome}>
                    <span className="dot" style={{ background: outcomeColors[index % outcomeColors.length] }} />
                    <span>{titleCase(outcome)}</span>
                    <strong>{count}</strong>
                  </div>
                ))}
                {!outcomeEntries.length ? <div className="legend-row"><span>No closed outcomes</span><strong>0</strong></div> : null}
              </div>
            </div>
          </div>
        </article>
        <article className="panel">
          <div className="panel-header"><h2 className="panel-title">Pipeline by stage</h2></div>
          <div className="chart-body">
            <div className="bars">
              {orderedStages.map((stage, index) => (
                <div className="bar-row" key={stage}>
                  <span>{titleCase(stage)}</span>
                  <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.round((stageCounts[stage] / maxStage) * 100)}%`, background: stageColors[index % stageColors.length] }} /></span>
                  <strong>{stageCounts[stage]}</strong>
                </div>
              ))}
            </div>
          </div>
        </article>
        <article className="panel">
          <div className="panel-header"><h2 className="panel-title">Tags</h2></div>
          <div className="tag-cloud">
            {Object.entries(tagCounts).length
              ? Object.entries(tagCounts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([tag, count]) => (
                <span className={`tag-chip tag-count ${tagColorClass(tag)}`} key={tag}>{tag} <strong>{count}</strong></span>
              ))
              : <TagChip tag="no-tags" />}
          </div>
        </article>
      </section>
    </section>
  );
}

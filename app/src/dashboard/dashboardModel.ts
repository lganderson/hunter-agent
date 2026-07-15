import { daysBetween, isWithinPastDays } from "../core/date";
import { DATA_QUALITY_TAGS, isClosed, normalize, tagList } from "../core/format";
import type { Action, AppState, Application } from "../core/types";

export type AttentionItem = {
  application: Application;
  reasons: string[];
  score: number;
};

export type DashboardModel = {
  activeCount: number;
  activeStageCounts: Record<string, number>;
  attentionItems: AttentionItem[];
  cleanupCount: number;
  closedCount: number;
  missingNextActionCount: number;
  openActions: Action[];
  outcomeEntries: Array<[string, number]>;
  overdueCount: number;
  recentApplicationCount: number;
  tagEntries: Array<[string, number]>;
  upcomingCount: number;
};

const priorityRank: Record<string, number> = { high: 0, medium: 1, low: 2 };

function attentionFor(application: Application, referenceDate: string): AttentionItem | null {
  const reasons: string[] = [];
  let score = 0;

  if (!normalize(application.next_action)) {
    reasons.push("No next action");
    score += 6;
  }
  if (application.is_overdue) {
    reasons.push("Action overdue");
    score += 5;
  }
  if (normalize(application.priority).toLowerCase() === "high") {
    reasons.push("High priority");
    score += 3;
  }
  if (normalize(application.stage).toLowerCase() === "posting-review") {
    reasons.push("Needs review");
    score += 2;
  }
  const age = daysBetween(application.date_applied, referenceDate);
  if (normalize(application.stage).toLowerCase() === "application-submitted" && age !== null && age >= 7) {
    reasons.push(`Submitted ${age}d ago`);
    score += 1;
  }

  return reasons.length ? { application, reasons, score } : null;
}

export function buildDashboardModel(data: AppState): DashboardModel {
  const activeStageCounts: Record<string, number> = {};
  const outcomeCounts: Record<string, number> = {};
  const tagCounts: Record<string, number> = {};
  const cleanupApplicationIds = new Set<string>();
  const attentionItems: AttentionItem[] = [];
  let activeCount = 0;
  let closedCount = 0;
  let missingNextActionCount = 0;
  let recentApplicationCount = 0;

  data.applications.forEach(application => {
    if (isWithinPastDays(application.date_applied, data.generated_date, 7)) recentApplicationCount += 1;
    if (isClosed(application)) {
      closedCount += 1;
      const outcome = normalize(application.outcome) || "blank";
      outcomeCounts[outcome] = (outcomeCounts[outcome] || 0) + 1;
      return;
    }

    activeCount += 1;
    const stage = normalize(application.stage) || "blank";
    activeStageCounts[stage] = (activeStageCounts[stage] || 0) + 1;
    if (!normalize(application.next_action)) missingNextActionCount += 1;
    tagList(application).forEach(tag => {
      if (DATA_QUALITY_TAGS.has(tag)) {
        cleanupApplicationIds.add(application.id);
      } else {
        tagCounts[tag] = (tagCounts[tag] || 0) + 1;
      }
    });

    const attention = attentionFor(application, data.generated_date);
    if (attention) attentionItems.push(attention);
  });

  const openActions = data.actions
    .filter(action => action.is_open)
    .sort((left, right) => {
      const dueDelta = left.sort_due.localeCompare(right.sort_due);
      if (dueDelta) return dueDelta;
      const priorityDelta = (priorityRank[left.priority] ?? 3) - (priorityRank[right.priority] ?? 3);
      return priorityDelta || left.company.localeCompare(right.company);
    });

  attentionItems.sort((left, right) => {
    const scoreDelta = right.score - left.score;
    if (scoreDelta) return scoreDelta;
    return left.application.sort_due.localeCompare(right.application.sort_due) || left.application.company.localeCompare(right.application.company);
  });

  return {
    activeCount,
    activeStageCounts,
    attentionItems,
    cleanupCount: cleanupApplicationIds.size,
    closedCount,
    missingNextActionCount,
    openActions,
    outcomeEntries: Object.entries(outcomeCounts).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0])),
    overdueCount: openActions.filter(action => action.is_overdue).length,
    recentApplicationCount,
    tagEntries: Object.entries(tagCounts).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0])).slice(0, 5),
    upcomingCount: openActions.filter(action => action.is_due_soon && !action.is_overdue).length
  };
}

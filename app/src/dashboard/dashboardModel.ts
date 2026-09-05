import { daysBetween, isWithinPastDays } from "../core/date";
import { DATA_QUALITY_TAGS, isClosed, normalize, tagList } from "../core/format";
import { routes } from "../core/routes";
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
  hunterSuggestions: HunterSuggestion[];
};

export type HunterSuggestion = {
  id: string;
  title: string;
  detail: string;
  actionLabel: string;
  to: string;
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
  if (normalize(application.stage).toLowerCase() === "considering") {
    reasons.push("Considering");
    score += 2;
  }
  const age = daysBetween(application.date_applied, referenceDate);
  if (normalize(application.stage).toLowerCase() === "applied" && age !== null && age >= 7) {
    reasons.push(`Submitted ${age}d ago`);
    score += 1;
  }

  return reasons.length ? { application, reasons, score } : null;
}

function metadataSuggestionCount(value: string): number {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

function buildHunterSuggestions(data: AppState): HunterSuggestion[] {
  const suggestions: HunterSuggestion[] = [];
  const dismissedIds = new Set(data.dismissed_suggestion_ids || []);

  data.discovery_preference_suggestions.forEach(suggestion => {
    suggestions.push({
      id: suggestion.id,
      title: `Refine ${suggestion.search_name || "Discovery"}`,
      detail: suggestion.reason,
      actionLabel: "Review search",
      to: routes.candidatesFiltered({ mode: "discovery", search_id: suggestion.search_id })
    });
  });

  data.companies.forEach(company => {
    if (company.decision_recommendation) {
      suggestions.push({
        id: `company-decision:${company.id}`,
        title: `Review ${company.name}`,
        detail: company.decision_recommendation,
        actionLabel: "Review company",
        to: routes.companyDetail(company.id)
      });
      return;
    }
    if (company.tracking_recommendation.startsWith("Hunter suggests tracking")) {
      suggestions.push({
        id: `company-tracking:${company.id}`,
        title: `Consider tracking ${company.name}`,
        detail: company.tracking_recommendation,
        actionLabel: "Review company",
        to: routes.companyDetail(company.id)
      });
    }
  });

  data.companies.forEach(company => {
    const count = company.company_metadata_suggestion_count
      ?? metadataSuggestionCount(company.company_metadata_suggestions_json || "");
    if (!count) return;
    suggestions.push({
      id: `company-research:${company.id}`,
      title: `Review research for ${company.name}`,
      detail: `${count} source-backed company detail${count === 1 ? "" : "s"} waiting for your decision.`,
      actionLabel: "Review research",
      to: routes.companyDetail(company.id)
    });
  });

  data.company_merge_suggestions.forEach(suggestion => {
    suggestions.push({
      id: `company-merge:${suggestion.id}`,
      title: "Possible duplicate companies",
      detail: `${suggestion.keep_company_name} and ${suggestion.merge_company_name} may be the same company.`,
      actionLabel: "Review match",
      to: routes.companyDetail(suggestion.keep_company_id)
    });
  });

  return suggestions.filter(suggestion => !dismissedIds.has(suggestion.id)).slice(0, 5);
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
    upcomingCount: openActions.filter(action => action.is_due_soon && !action.is_overdue).length,
    hunterSuggestions: buildHunterSuggestions(data)
  };
}

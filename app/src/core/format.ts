import type { Action, Application } from "./types";

export const COMPLETED_ACTION_STATUSES = new Set(["done", "completed", "cancelled", "skipped"]);

export function normalize(value: unknown): string {
  return (value || "").toString().trim();
}

export function titleCase(value: unknown): string {
  const text = normalize(value);
  if (!text) return "(blank)";
  return text.replace(/[-_]+/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}

export function cssClass(value: unknown): string {
  return normalize(value).toLowerCase().replace(/[^a-z0-9]+/g, "-") || "blank";
}

export function normalizeTag(value: unknown): string {
  return normalize(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export function tagColorClass(value: unknown): string {
  const tag = normalizeTag(value);
  if (!tag) return "tag-color-muted";
  let hash = 0;
  for (let index = 0; index < tag.length; index += 1) {
    hash = (hash * 31 + tag.charCodeAt(index)) % 9973;
  }
  return `tag-color-${(hash % 8) + 1}`;
}

export function tagList(app: Application): string[] {
  if (Array.isArray(app.tag_list)) return app.tag_list;
  return normalize(app.tags).split(",").map(tag => tag.trim()).filter(Boolean);
}

export function dueLabel(app: Application): string {
  if (!app.next_action_date) return "";
  if (app.days_until_next_action === null) return app.next_action_date;
  if (app.days_until_next_action < 0) return `${app.next_action_date} (${Math.abs(app.days_until_next_action)}d overdue)`;
  if (app.days_until_next_action === 0) return `${app.next_action_date} (today)`;
  if (app.days_until_next_action <= 7) return `${app.next_action_date} (in ${app.days_until_next_action}d)`;
  return app.next_action_date;
}

export function actionDueLabel(action: Action): string {
  if (!action.due_date) return "";
  if (action.days_until_due === null) return action.due_date;
  if (action.days_until_due < 0) return `${action.due_date} (${Math.abs(action.days_until_due)}d overdue)`;
  if (action.days_until_due === 0) return `${action.due_date} (today)`;
  if (action.days_until_due <= 7) return `${action.due_date} (in ${action.days_until_due}d)`;
  return action.due_date;
}

export function dateOnlyLabel(value: string): string {
  const match = normalize(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return normalize(value);
  const year = Number.parseInt(match[1], 10);
  const monthIndex = Number.parseInt(match[2], 10) - 1;
  const day = Number.parseInt(match[3], 10);
  const date = new Date(year, monthIndex, day);
  const currentYear = new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(year === currentYear ? {} : { year: "numeric" })
  });
}

export function isClosed(app: Application): boolean {
  return normalize(app.stage).toLowerCase() === "closed";
}

export function isActionComplete(action: Action): boolean {
  return COMPLETED_ACTION_STATUSES.has(normalize(action.status).toLowerCase());
}

export function inlineMarkdown(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

export function markdownToHtml(markdown: string): string {
  const lines = normalize(markdown).split(/\r?\n/);
  const out: string[] = [];
  let inList = false;
  function closeList() {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  }
  lines.forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      return;
    }
    if (trimmed.startsWith("# ")) {
      closeList();
      out.push(`<h1>${escapeHtml(trimmed.slice(2))}</h1>`);
    } else if (trimmed.startsWith("## ")) {
      closeList();
      out.push(`<h2>${escapeHtml(trimmed.slice(3))}</h2>`);
    } else if (trimmed.startsWith("- ")) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inlineMarkdown(trimmed.slice(2))}</li>`);
    } else {
      closeList();
      out.push(`<p>${inlineMarkdown(trimmed)}</p>`);
    }
  });
  closeList();
  return out.join("");
}

function escapeHtml(value: unknown): string {
  return normalize(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

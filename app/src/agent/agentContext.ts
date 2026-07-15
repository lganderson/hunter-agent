import type { AgentContext, AppState } from "../core/types";

export type AgentStarter = {
  label: string;
  prompt: string;
};

export type AgentExperience = {
  title: string;
  description: string;
  starters: AgentStarter[];
};

export function buildAgentContext(pathname: string, search: string, data: AppState): AgentContext {
  const segments = pathname.split("/").filter(Boolean).map(segment => decodeURIComponent(segment));
  const query = Object.fromEntries(new URLSearchParams(search));
  const base = { pathname, query };

  if (!segments.length) return { ...base, route: "dashboard", label: "Dashboard" };
  if (segments[0] === "postings" && segments[1] && segments[1] !== "new") {
    const posting = data.applications.find(item => item.id === segments[1]);
    return {
      ...base,
      route: "posting-detail",
      entity_type: "posting",
      entity_id: segments[1],
      label: posting ? [posting.company, posting.role].filter(Boolean).join(" · ") : segments[1]
    };
  }
  if (segments[0] === "postings") {
    return { ...base, route: segments[1] === "new" ? "posting-new" : "postings", label: segments[1] === "new" ? "Add posting" : "Postings" };
  }
  if (segments[0] === "companies" && segments[1] && segments[1] !== "new") {
    const company = data.companies.find(item => item.id === segments[1]);
    return {
      ...base,
      route: "company-detail",
      entity_type: "company",
      entity_id: segments[1],
      label: company?.name || segments[1]
    };
  }
  if (segments[0] === "companies") {
    return { ...base, route: segments[1] === "new" ? "company-new" : "companies", label: segments[1] === "new" ? "Add company" : "Companies" };
  }
  if (segments[0] === "candidates") return { ...base, route: "candidates", label: "Posting candidates" };
  if (segments[0] === "actions") return { ...base, route: "actions", label: "Actions" };
  if (segments[0] === "contacts") return { ...base, route: "contacts", label: "Contacts" };
  if (segments[0] === "settings") return { ...base, route: "settings", label: "Search goals and settings" };
  return { ...base, route: "unknown", label: "Hunter" };
}

export function agentExperience(context: AgentContext, data: AppState): AgentExperience {
  const openActions = data.actions.filter(action => action.is_open);
  const overdueActions = openActions.filter(action => action.is_overdue);
  const activePostings = data.applications.filter(posting => posting.is_active);

  switch (context.route) {
    case "dashboard":
      return {
        title: "What matters today?",
        description: `${openActions.length} open actions · ${overdueActions.length} overdue · ${activePostings.length} active postings`,
        starters: [
          { label: "Plan my day", prompt: "Help me prioritize today's actions into a realistic plan." },
          { label: "Triage overdue work", prompt: "Triage my overdue actions and recommend what to do, reschedule, or close." },
          { label: "Review my pipeline", prompt: "Give me a concise weekly review of my active job-search pipeline." }
        ]
      };
    case "posting-detail":
      return {
        title: "Posting review",
        description: "Fit, evidence gaps, and next action.",
        starters: [
          { label: "Review role fit", prompt: "Review the fit for this posting using my Search Goals and resume evidence." },
          { label: "Find evidence gaps", prompt: "What evidence is missing for this posting, and how should I position myself?" },
          { label: "Choose the next move", prompt: "Recommend the next concrete action for this posting." }
        ]
      };
    case "company-detail":
      return {
        title: "Company review",
        description: "Open roles, fit, and next steps.",
        starters: [
          { label: "Check careers", prompt: "Check this company's careers page and summarize what changed." },
          { label: "Show recommended roles", prompt: "Show me the recommended candidates for this company and explain the strongest fits." },
          { label: "Recommend a next step", prompt: "What should I do next with this company?" }
        ]
      };
    case "candidates":
      return {
        title: "Candidate review",
        description: "Compare fit and clear the review queue.",
        starters: [
          { label: "Show strongest fits", prompt: "Show me the strongest recommended candidates and explain why they stand out." },
          { label: "Compare top candidates", prompt: "Compare the top candidates by fit, evidence, and urgency." },
          { label: "Prioritize my review", prompt: "Which candidate reviews should I clear first?" }
        ]
      };
    case "actions":
      return {
        title: "Action plan",
        description: `${openActions.length} open actions · ${overdueActions.length} overdue`,
        starters: [
          { label: "Prioritize actions", prompt: "Prioritize my open actions and explain the top three." },
          { label: "Reschedule realistically", prompt: "Help me reschedule overdue actions into a realistic week." },
          { label: "Find stale work", prompt: "Which open actions are stale, redundant, or safe to close?" }
        ]
      };
    case "postings":
      return {
        title: "Pipeline review",
        description: `${activePostings.length} active postings`,
        starters: [
          { label: "Find stalled postings", prompt: "Which tracked postings are stalled, and what should I do about each one?" },
          { label: "Compare active roles", prompt: "Compare my strongest active postings and recommend where to focus." },
          { label: "Check data quality", prompt: "Find tracked postings that are missing important information or a clear next action." }
        ]
      };
    case "companies":
      return {
        title: "Company review",
        description: "Priorities, openings, contacts, and coverage.",
        starters: [
          { label: "Prioritize companies", prompt: "Which companies deserve the most attention right now, and why?" },
          { label: "Find unchecked careers", prompt: "Which interested companies need a careers-page check?" },
          { label: "Review company coverage", prompt: "Where am I missing contacts, careers sources, or active postings across target companies?" }
        ]
      };
    case "contacts":
      return {
        title: "Contact follow-ups",
        description: "Follow-ups and relevant relationships.",
        starters: [
          { label: "Find follow-ups", prompt: "Which contacts need a follow-up, and what should the next step be?" },
          { label: "Find relevant contacts", prompt: "Which contacts are relevant to my most important active postings?" },
          { label: "Review relationship gaps", prompt: "Where are my active postings missing a useful contact relationship?" }
        ]
      };
    case "settings":
      return {
        title: "Search goals",
        description: "Search criteria and fit signals.",
        starters: [
          { label: "Review my goals", prompt: "Review my current Search Goals for clarity, focus, and contradictions." },
          { label: "Tune fit signals", prompt: "Recommend improvements to my fit signals based on my Search Goals." },
          { label: "Explain my criteria", prompt: "Summarize how Hunter currently judges role fit." }
        ]
      };
    default:
      return {
        title: "What’s next?",
        description: "Review your tracker or choose a next move.",
        starters: [
          { label: "Plan my next move", prompt: "What is the most useful next move in my job search?" },
          { label: "Review open work", prompt: "Review my open actions and active postings." },
          { label: "Find missing context", prompt: "What important information is missing from my tracker?" }
        ]
      };
  }
}

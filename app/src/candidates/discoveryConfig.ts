import type { DiscoveryCandidateDetails, DiscoverySearchLaneDefinition } from '../core/types';

export type DiscoveryFilter = "needs-decision" | "pursued" | "ignored" | "duplicate" | "unavailable";

export const DISCOVERY_FILTERS: Array<{ id: DiscoveryFilter; label: string }> = [
  { id: "needs-decision", label: "Needs decision" },
  { id: "pursued", label: "Considering" },
  { id: "ignored", label: "Ignored" },
];

export const DISCOVERY_FILTER_VALUES: DiscoveryFilter[] = ["needs-decision", "pursued", "ignored", "duplicate", "unavailable"];

export const DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES = new Set(["not-interested", "archived"]);

export const WORK_MODE_OPTIONS: Array<{ id: DiscoverySearchLaneDefinition["work_modes"][number]; label: string }> = [
  { id: "on-site", label: "On-site" },
  { id: "hybrid", label: "Hybrid" },
  { id: "remote", label: "Remote" }
];

export const ROLE_FAMILY_OPTIONS = [
  { id: "technical-program", label: "Technical program leadership", description: "TPM through staff, principal, and lead levels" },
  { id: "engineering-delivery", label: "Engineering delivery", description: "Engineering programs, technical projects, and delivery leads" },
  { id: "product-platform", label: "Product and platform strategy", description: "Senior and principal product, technical product, platform product, and product strategy leads" },
  { id: "product-operations", label: "Product systems and operations", description: "Product ops, product systems, development operations, and enablement builders" },
  { id: "technologist-prototyping", label: "Technologist and prototyping", description: "Product, creative, and design technologists plus prototyping and innovation leads" },
  { id: "customer-implementation", label: "Customer implementation", description: "Technical solutions, engagement, and implementation programs" },
  { id: "games-interactive", label: "Games and interactive delivery", description: "Technical producers, game producers, and development directors" },
  { id: "systems-hardware", label: "Systems and product development", description: "Systems programs, product development, and NPI" }
] as const;

export const EMPTY_DETAILS: DiscoveryCandidateDetails = {
  company_id: "",
  company_name: "",
  title: "",
  canonical_url: "",
  location: "",
  work_mode: "",
  description_text: "",
  notes: ""
};

export const MAX_BULK_INGEST = 25;

export const DISMISSED_DISCOVERY_RUN_KEY = "hunter-dismissed-discovery-run-v1";

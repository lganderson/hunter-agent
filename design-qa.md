# Discovery completion redesign — design QA

## Reference and capture

- Source mockup: Codex-generated design reference (local, not committed)
- Browser implementation: `data/design-qa/discovery-implementation.png` (local QA artifact, not committed)
- Combined comparison: `data/design-qa/discovery-comparison.png` (local QA artifact, not committed)
- Implementation URL: `http://127.0.0.1:8012/candidates?mode=discovery`
- Viewport/capture: 1496 × 817 CSS pixels, 1× browser screenshot density
- Source dimensions: 2079 × 756 pixels; normalized to the 1496-pixel implementation width in the combined comparison
- State: Discovery mode; Emerging Technology search; latest run complete; completion details collapsed; Needs decision selected; Match descending

## Full-view comparison

The implementation preserves the existing Hunter shell, compact density, filters, status tabs, and live candidate table while matching the selected mockup's hierarchy: modes, search controls, concise context, single completion row, filters, and review queue. Completed global company-evaluation and candidate-enrichment banners no longer compete with the page. The mockup's sample table headings were not used to replace Hunter's existing candidate schema.

## Focused comparison

- Search context matches the human-readable target: `Emerging Technology · Minnesota + US remote`.
- Completion copy matches the saved run: `37 new roles · 11 enriched · 5 need review`.
- The completion row is one compact, dismissible status surface with a secondary `View details` action.
- Search, multi-filters, decision tabs, and `Review 10 of 85 ready` retain the mockup's order and relative emphasis.
- Existing spacing and typography tokens were retained; the implementation is slightly denser than the generated mockup to preserve table space and stay consistent with the current app.

## Interaction and state checks

- `View details` expands six readable run metrics and changes to `Hide details`.
- Dismiss removes the completion row immediately.
- Dismiss remains effective after a full page refresh.
- The persisted latest-run summary renders without rerunning Discovery or modifying candidate data.
- Completed global jobs are hidden; active and failed jobs remain available for progress or recovery feedback.

## Findings and iteration history

1. Initial implementation replaced the long operation paragraph with a structured completion surface sourced from `last_run_summary`.
2. Typecheck found unreachable completed-state label branches after completed banners were intentionally suppressed; those branches were simplified.
3. Browser comparison found no priority 0–2 visual or interaction issues. The denser live table and persistent navigation are intentional existing-product constraints, not regressions.

Final result: passed

export type SortDirection = "asc" | "desc";

export type SortState<Key extends string> = {
  key: Key;
  direction: SortDirection;
};

export function nextSortState<Key extends string>(
  current: SortState<Key>,
  key: Key,
  initialDirection: SortDirection = "asc"
): SortState<Key> {
  if (current.key !== key) return { key, direction: initialDirection };
  return { key, direction: current.direction === "asc" ? "desc" : "asc" };
}

export function compareText(left: unknown, right: unknown, direction: SortDirection): number {
  const leftText = String(left || "").trim();
  const rightText = String(right || "").trim();
  if (!leftText && !rightText) return 0;
  if (!leftText) return 1;
  if (!rightText) return -1;
  const result = leftText.localeCompare(rightText, undefined, { numeric: true, sensitivity: "base" });
  return direction === "asc" ? result : -result;
}

export function compareNumber(left: unknown, right: unknown, direction: SortDirection): number {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const leftValid = Number.isFinite(leftNumber);
  const rightValid = Number.isFinite(rightNumber);
  if (!leftValid && !rightValid) return 0;
  if (!leftValid) return 1;
  if (!rightValid) return -1;
  const result = leftNumber - rightNumber;
  return direction === "asc" ? result : -result;
}

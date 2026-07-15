export function dateOrdinal(value: string): number | null {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return null;
  return Math.floor(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) / 86_400_000);
}

export function daysBetween(earlier: string, later: string): number | null {
  const earlierOrdinal = dateOrdinal(earlier);
  const laterOrdinal = dateOrdinal(later);
  if (earlierOrdinal === null || laterOrdinal === null) return null;
  return laterOrdinal - earlierOrdinal;
}

export function isWithinPastDays(value: string, reference: string, days: number): boolean {
  const difference = daysBetween(value, reference);
  return difference !== null && difference >= 0 && difference < days;
}

import { useCallback, useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type { SortDirection, SortState } from "./tableSort";

const VIEW_STATE_STORAGE_PREFIX = "hunter-view-state-v1:";

export type ViewParamUpdates = Record<string, string | number | boolean | null | undefined>;

function readStoredQuery(viewKey: string): string {
  try {
    return window.localStorage.getItem(`${VIEW_STATE_STORAGE_PREFIX}${viewKey}`) || "";
  } catch {
    return "";
  }
}

function storeQuery(viewKey: string, query: string) {
  try {
    if (query) window.localStorage.setItem(`${VIEW_STATE_STORAGE_PREFIX}${viewKey}`, query);
    else window.localStorage.removeItem(`${VIEW_STATE_STORAGE_PREFIX}${viewKey}`);
  } catch {
    // URL state remains available when local storage is unavailable.
  }
}

export function usePersistentViewParams(viewKey: string) {
  const location = useLocation();
  const storedQuery = useRef(location.search ? "" : readStoredQuery(viewKey)).current;
  const [params, setSearchParams] = useSearchParams(storedQuery);
  const query = params.toString();
  const restoredStoredQuery = useRef(false);

  useEffect(() => {
    if (restoredStoredQuery.current) return;
    restoredStoredQuery.current = true;
    if (!location.search && storedQuery) {
      setSearchParams(storedQuery, { replace: true });
    }
  }, [location.search, setSearchParams, storedQuery]);

  useEffect(() => {
    storeQuery(viewKey, query);
  }, [query, viewKey]);

  const updateParams = useCallback((updates: ViewParamUpdates) => {
    setSearchParams(current => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === "") next.delete(key);
        else next.set(key, String(value));
      }
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const clearParams = useCallback(() => {
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  return { params, updateParams, clearParams };
}

export function selectionFromParam(value: string | null, options: string[], fallback: string[]) {
  if (!value) return fallback;
  if (value === "all") return options;
  if (value === "none") return [];
  const requested = value.split(",").filter(item => options.includes(item));
  return requested.length ? requested : fallback;
}

export function selectionParamValue(selected: string[], options: string[], fallback: string[]) {
  if (sameSelection(selected, fallback)) return null;
  if (sameSelection(selected, options)) return "all";
  if (!selected.length) return "none";
  return selected.join(",");
}

export function sortFromParams<Key extends string>(
  params: URLSearchParams,
  keyName: string,
  directionName: string,
  allowedKeys: readonly Key[],
  fallback: SortState<Key>
): SortState<Key> {
  const requestedKey = params.get(keyName);
  const requestedDirection = params.get(directionName);
  return {
    key: allowedKeys.includes(requestedKey as Key) ? requestedKey as Key : fallback.key,
    direction: requestedDirection === "asc" || requestedDirection === "desc"
      ? requestedDirection as SortDirection
      : fallback.direction
  };
}

function sameSelection(left: string[], right: string[]) {
  return left.length === right.length && left.every(value => right.includes(value));
}

import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readModelQueryKeys as keys } from "../core/queryKeys";
import { useCompanyCandidateDecisions, useDiscoveryCandidateDecisions } from "./useCandidateDecisions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 3 } } });
  let revision = 0;
  const affectedKeys = [keys.candidateList("company"), keys.candidateList("discovery"),
    keys.candidateDetail("company", "CP1"), keys.candidateDetail("discovery", "DC1"),
    keys.appShell(), keys.entityDetail("company", "CO1"), keys.entityDetail("application", "A1"), keys.entityDetail("action", "T1")];
  const readers = affectedKeys.map(queryKey => {
    client.setQueryData(queryKey, { revision: 0 });
    const queryFn = vi.fn(async () => ({ revision }));
    const observer = new QueryObserver(client, { queryKey, queryFn });
    const unsubscribe = observer.subscribe(() => {});
    return { queryKey, queryFn, unsubscribe };
  });
  const contactKey = keys.entityDetail("contact", "C1");
  client.setQueryData(contactKey, { revision: 0 });
  const fetcher = vi.fn(async () => {
    revision++;
    return new Response(JSON.stringify({ candidate: { id: "DC1", status: "ignored" }, posting: { id: "A1" } }));
  });
  vi.stubGlobal("fetch", fetcher);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(() => ({ company: useCompanyCandidateDecisions(), discovery: useDiscoveryCandidateDecisions() }), { wrapper });
  return { ...hook, client, readers, contactKey, fetcher,
    interruptResponse: () => fetcher.mockImplementation(async () => { revision++; throw new TypeError("connection lost"); }),
    dispose: () => { readers.forEach(reader => reader.unsubscribe()); client.clear(); }
  };
}

type Decisions = ReturnType<typeof useCompanyCandidateDecisions>;
type Discovery = ReturnType<typeof useDiscoveryCandidateDecisions>;
const operations: [string, (company: Decisions, discovery: Discovery) => Promise<unknown>][] = [
  ["company Ignore", c => c.setStatus("CP1", "ignored")],
  ["company restore", c => c.setStatus("CP1", "new")],
  ["company bulk Ignore", c => c.setStatuses(["CP1", "CP2"], "ignored")],
  ["company Consider", c => c.pursue("CP1")],
  ["Discovery Ignore", (_, d) => d.setStatus("DC1", "ignored")],
  ["Discovery bulk restore", (_, d) => d.setStatuses(["DC1", "DC2"], "new")],
  ["Discovery Consider", (_, d) => d.pursue("DC1")],
  ["Discovery duplicate", (_, d) => d.markDuplicate("DC1", "A1")],
  ["Discovery Undo", (_, d) => d.undo("DC1", "pursued", "A1", true)]
];

describe("candidate decisions", () => {
  it.each(operations)("%s refreshes both pools and linked views before resolving", async (_, operation) => {
    const state = setup();
    try {
      await act(async () => { await operation(state.result.current.company, state.result.current.discovery); });
      expect(state.fetcher).toHaveBeenCalledTimes(1);
      for (const reader of state.readers) {
        expect(reader.queryFn).toHaveBeenCalledTimes(1);
        expect(state.client.getQueryData(reader.queryKey)).toEqual({ revision: 1 });
      }
      expect(state.client.getQueryState(state.contactKey)?.isInvalidated).toBe(false);
    } finally { state.dispose(); }
  });

  it("reconciles an interrupted write without replaying it", async () => {
    const state = setup();
    state.interruptResponse();
    try {
      await act(async () => {
        await expect(state.result.current.discovery.setStatus("DC1", "ignored")).rejects.toThrow("may have completed");
      });
      expect(state.fetcher).toHaveBeenCalledTimes(1);
      for (const reader of state.readers) expect(state.client.getQueryData(reader.queryKey)).toEqual({ revision: 1 });
    } finally { state.dispose(); }
  });

  it("refreshes once after a partially successful batch and reports the failed IDs", async () => {
    const state = setup();
    const progress = vi.fn();
    state.fetcher.mockRejectedValueOnce(new TypeError("connection lost"));
    try {
      await act(async () => {
        const batch = await state.result.current.company.pursueMany(["CP1", "CP2", "CP3"], progress);
        expect(batch.failedIds).toEqual(["CP1"]);
        expect(batch.results).toHaveLength(2);
      });
      expect(state.fetcher).toHaveBeenCalledTimes(3);
      expect(progress.mock.calls).toEqual([[0, 3], [1, 3], [2, 3]]);
      for (const reader of state.readers) {
        expect(reader.queryFn).toHaveBeenCalledTimes(1);
        expect(state.client.getQueryData(reader.queryKey)).toEqual({ revision: 2 });
      }
    } finally { state.dispose(); }
  });
});

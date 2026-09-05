import { useCallback, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

type Job = { id: string; status: string };

/** One cached job per worker; poll quickly only while work is active. */
export function useBackgroundJob<T extends Job>(
  worker: string,
  fetchJob: () => Promise<{ job: T | null }>,
  onCompleted: () => Promise<unknown>
): readonly [T | null, (job: T | null) => void] {
  const client = useQueryClient();
  const completed = useRef("");
  const query = useQuery({
    queryKey: ["background-job", worker],
    queryFn: fetchJob,
    refetchOnMount: "always",
    refetchInterval: query => ["queued", "running"].includes(query.state.data?.job?.status || "") ? 1_000 : 10_000
  });
  const job = query.data?.job || null;
  useEffect(() => {
    if (job?.status !== "completed" || completed.current === job.id) return;
    completed.current = job.id;
    void onCompleted().catch(() => {
      // Keep a failed refresh eligible for the next job poll.
      completed.current = "";
    });
  }, [job, onCompleted, query.dataUpdatedAt]);
  const setJob = useCallback((job: T | null) => {
    client.setQueryData(["background-job", worker], { job });
  }, [client, worker]);
  return [job, setJob];
}

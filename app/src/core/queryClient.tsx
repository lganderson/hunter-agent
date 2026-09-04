import { QueryCache, QueryClient, QueryClientProvider, type InfiniteData } from "@tanstack/react-query";
import type { CandidatePage, CandidatePool } from "./readModelTypes";
import { useState, type PropsWithChildren } from "react";
import { ReadModelApiError } from "./readModelApi";

export function createHunterQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (error instanceof ReadModelApiError && error.code === "cursor_expired"
          && query.queryKey[0] === "read-models" && query.queryKey[1] === "candidates"
          && query.queryKey[3] === "list") {
          // A cursor belongs to one database revision. Restart the list with
          // its existing filters; retrying that cursor can never succeed.
          const previous = query.state.data as InfiniteData<CandidatePage<CandidatePool>> | undefined;
          if (!previous || previous.pageParams[0]) return;
          // Retain the first page while it reloads so filter controls keep
          // focus and the route does not unmount during recovery.
          client.setQueryData(query.queryKey, { pages: previous.pages.slice(0, 1), pageParams: [undefined] });
          void client.invalidateQueries({ queryKey: query.queryKey, exact: true });
        }
      }
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: (failureCount, error) =>
          error instanceof ReadModelApiError && error.status === 409
          && error.code !== "cursor_expired" && failureCount < 1,
        retryDelay: 150,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false
      },
      mutations: {
        retry: false
      }
    }
  });
  return client;
}

type HunterQueryProviderProps = PropsWithChildren<{ client?: QueryClient }>;

export function HunterQueryProvider({ children, client: suppliedClient }: HunterQueryProviderProps) {
  const [client] = useState(() => suppliedClient ?? createHunterQueryClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

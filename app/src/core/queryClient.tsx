import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type PropsWithChildren } from "react";
import { ReadModelApiError } from "./readModelApi";

export function createHunterQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: (failureCount, error) =>
          error instanceof ReadModelApiError && error.status === 409 && failureCount < 1,
        retryDelay: 150,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false
      },
      mutations: {
        retry: false
      }
    }
  });
}

type HunterQueryProviderProps = PropsWithChildren<{ client?: QueryClient }>;

export function HunterQueryProvider({ children, client: suppliedClient }: HunterQueryProviderProps) {
  const [client] = useState(() => suppliedClient ?? createHunterQueryClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

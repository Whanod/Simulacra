"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Terminal-run views never change; lean on long stale + manual
        // invalidation for live runs (see refetchInterval guidance in the
        // postgres-migration plan, Phase 4).
        staleTime: 60_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}

export default function QueryProvider({ children }: { children: ReactNode }) {
  // Lazy init keeps the client stable across re-renders and gives each
  // SSR request its own instance (Next.js App Router pattern).
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

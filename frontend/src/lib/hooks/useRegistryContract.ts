"use client";

/**
 * Shared registry contract hook (US-013).
 *
 * The builder page has several independent components that each
 * need the registry contract (agent groups designer, every
 * registry-driven select). This hook caches the fetch at module
 * level so only one request goes out per session, and exposes
 * `{ contract, error, reload }` for consumers.
 */

import { useEffect, useState } from "react";
import type { RegistryContractResponse } from "@/lib/types/contract";
import { registryService } from "@/lib/services/registryService";

let cached: RegistryContractResponse | null = null;
let inflight: Promise<RegistryContractResponse> | null = null;

type Subscriber = (value: RegistryContractResponse | null, error: string | null) => void;
const subscribers = new Set<Subscriber>();

function notify(value: RegistryContractResponse | null, error: string | null) {
  for (const sub of subscribers) sub(value, error);
}

function startLoad(): Promise<RegistryContractResponse> {
  if (cached) return Promise.resolve(cached);
  if (inflight) return inflight;
  inflight = registryService
    .getContract()
    .then((resp) => {
      cached = resp;
      inflight = null;
      notify(resp, null);
      return resp;
    })
    .catch((err: unknown) => {
      inflight = null;
      const message = err instanceof Error ? err.message : String(err);
      notify(null, message);
      throw err;
    });
  return inflight;
}

export interface UseRegistryContractResult {
  contract: RegistryContractResponse | null;
  error: string | null;
  reload: () => void;
}

export function useRegistryContract(): UseRegistryContractResult {
  const [contract, setContract] = useState<RegistryContractResponse | null>(cached);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const subscriber: Subscriber = (value, err) => {
      if (cancelled) return;
      setContract(value);
      setError(err);
    };
    subscribers.add(subscriber);
    if (!cached) {
      startLoad().catch(() => {
        // Errors are propagated through the subscriber fanout.
      });
    }
    return () => {
      cancelled = true;
      subscribers.delete(subscriber);
    };
  }, []);

  const reload = () => {
    cached = null;
    inflight = null;
    startLoad().catch(() => {
      // Errors are propagated through the subscriber fanout.
    });
  };

  return { contract, error, reload };
}

/** Test-only hook — clears the module-level cache between tests. */
export function __resetRegistryContractCache(): void {
  cached = null;
  inflight = null;
  subscribers.clear();
}

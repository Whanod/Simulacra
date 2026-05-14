/**
 * Wrapper over `GET /runs/{id}/events` and `GET /runs/{id}/correlations/{cid}`.
 * Filtered event log (post-paint) and correlation drill-down for the
 * results page — the overview view doesn't carry the full event stream,
 * only its per-type counts under `event_summary`.
 */

import { apiFetch } from "@/lib/api/client";
import { fromApiEvents, type ApiEventRaw } from "@/lib/api/adapters/runs";
import type { EvEntry } from "@/lib/types";

export interface FetchEventsParams {
  eventType?: string;
  agentId?: string;
  round?: number;
  fromRound?: number;
  toRound?: number;
  cursor?: number;
  limit?: number;
  offset?: number;
}

export interface EventsPage {
  runId: string;
  events: EvEntry[];
  raw: ApiEventRaw[];
  // Present only when more rows likely exist — the backend omits the
  // field once a partial page lands. Treat absence as "end of stream".
  nextCursor: number | null;
}

export interface CorrelationChain {
  runId: string;
  correlationId: string;
  events: EvEntry[];
  raw: ApiEventRaw[];
}

interface ApiEventsResponse {
  run_id: string;
  events: ApiEventRaw[];
  next_cursor?: number;
}

interface ApiCorrelationResponse {
  run_id: string;
  correlation_id: string;
  events: ApiEventRaw[];
}

export const eventsService = {
  async fetchEvents(
    runId: string,
    params: FetchEventsParams = {},
  ): Promise<EventsPage> {
    const resp = await apiFetch<ApiEventsResponse>(`/runs/${runId}/events`, {
      query: {
        event_type: params.eventType,
        agent_id: params.agentId,
        round: params.round,
        from: params.fromRound,
        to: params.toRound,
        cursor: params.cursor,
        limit: params.limit,
        offset: params.offset,
      },
    });
    const raw = resp.events || [];
    return {
      runId: resp.run_id,
      events: fromApiEvents(raw),
      raw,
      nextCursor: resp.next_cursor ?? null,
    };
  },

  async fetchCorrelation(
    runId: string,
    correlationId: string,
  ): Promise<CorrelationChain> {
    const resp = await apiFetch<ApiCorrelationResponse>(
      `/runs/${runId}/correlations/${encodeURIComponent(correlationId)}`,
    );
    const raw = resp.events || [];
    return {
      runId: resp.run_id,
      correlationId: resp.correlation_id,
      events: fromApiEvents(raw),
      raw,
    };
  },
};

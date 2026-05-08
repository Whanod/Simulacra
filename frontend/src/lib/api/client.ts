import { API_BASE_URL } from "@/lib/config";
import { ApiError } from "@/lib/api/errors";

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: ApiFetchOptions["query"]): string {
  const base = path.startsWith("http") ? path : `${API_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
  if (!query) return base;
  const entries = Object.entries(query).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return base;
  const usp = new URLSearchParams();
  for (const [k, v] of entries) usp.append(k, String(v));
  return `${base}${base.includes("?") ? "&" : "?"}${usp.toString()}`;
}

async function parseError(res: Response): Promise<ApiError> {
  const contentType = res.headers.get("content-type") || "";
  let detail: unknown;
  try {
    if (contentType.includes("application/json")) {
      const json = (await res.json()) as { detail?: unknown; message?: unknown };
      detail = json?.detail ?? json?.message ?? json;
    } else {
      detail = await res.text();
    }
  } catch {
    detail = undefined;
  }
  const message =
    typeof detail === "string" && detail.length > 0
      ? detail
      : `${res.status} ${res.statusText || "Request failed"}`;
  return new ApiError(res.status, message, detail);
}

export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { body, query, headers, ...rest } = options;
  const url = buildUrl(path, query);
  const init: RequestInit = {
    ...rest,
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers || {}),
    },
  };
  if (body !== undefined) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new TypeError(
      err instanceof Error ? err.message : `Network error calling ${url}`,
    );
  }

  if (!res.ok) throw await parseError(res);

  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export async function apiFetchBlob(
  path: string,
  options: ApiFetchOptions = {},
): Promise<Blob> {
  const { body, query, headers, ...rest } = options;
  const url = buildUrl(path, query);
  const init: RequestInit = {
    ...rest,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers || {}),
    },
  };
  if (body !== undefined) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) throw await parseError(res);
  return await res.blob();
}

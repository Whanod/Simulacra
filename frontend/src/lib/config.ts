const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export const API_BASE_URL: string = stripTrailingSlash(
  process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_BASE_URL,
);

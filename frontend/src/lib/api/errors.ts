export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function toToastMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return "Not found.";
    if (err.status === 409) return "Conflict — resource already in use.";
    if (err.status >= 500) return `Server error (${err.status}). Try again.`;
    if (typeof err.detail === "string" && err.detail.length > 0) return err.detail;
    return err.message || `Request failed (${err.status}).`;
  }
  if (err instanceof TypeError) return "Network error — is the backend running?";
  if (err instanceof Error) return err.message;
  return "Unknown error.";
}

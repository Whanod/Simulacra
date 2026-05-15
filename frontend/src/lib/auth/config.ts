// Privy app id for the React SDK. When unset, the frontend runs in
// "open mode": the auth provider becomes a transparent shim, the gate
// modal is never mounted, and every request goes out unauthenticated.
// This mirrors the backend's open-mode contract (PRIVY_APP_ID unset on
// the server) so local dev / vitest / playwright keep working with
// zero configuration.
export const PRIVY_APP_ID: string | undefined =
  process.env.NEXT_PUBLIC_PRIVY_APP_ID || undefined;

export const isPrivyConfigured = (): boolean => Boolean(PRIVY_APP_ID);

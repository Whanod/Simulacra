// Pure state machine for the AuthModal. Lives in its own file so unit
// tests can drive transitions without mocking the Privy SDK or the DOM.

export type AuthState =
  | {
      kind: "email";
      email: string;
      submitting: boolean;
      error: string | null;
    }
  | {
      kind: "awaitingOtp";
      email: string;
      digits: string[];
      submitting: boolean;
      error: string | null;
      // Seconds remaining before the resend link is enabled. Counts
      // down to zero, then stays at zero until the user clicks resend.
      resendIn: number;
    }
  | {
      kind: "success";
    };

export type AuthAction =
  | { type: "EMAIL_CHANGE"; email: string }
  | { type: "SEND_BEGIN"; email: string }
  | { type: "SEND_OK"; resendIn: number }
  | { type: "SEND_FAIL"; error: string }
  | { type: "OTP_CHANGE"; digits: string[] }
  | { type: "VERIFY_BEGIN" }
  | { type: "VERIFY_OK" }
  | { type: "VERIFY_FAIL"; error: string }
  | { type: "RESEND_TICK" }
  | { type: "EDIT_EMAIL" }
  | { type: "RESET" };

export const initialAuthState: AuthState = {
  kind: "email",
  email: "",
  submitting: false,
  error: null,
};

export function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case "EMAIL_CHANGE":
      if (state.kind !== "email") return state;
      return { ...state, email: action.email, error: null };

    case "SEND_BEGIN":
      // From email → email (with submitting). Or from awaitingOtp →
      // awaitingOtp during resend.
      if (state.kind === "email") {
        return { ...state, email: action.email, submitting: true, error: null };
      }
      if (state.kind === "awaitingOtp") {
        return { ...state, submitting: true, error: null };
      }
      return state;

    case "SEND_OK":
      // Transition into the OTP screen with the resend timer started.
      // Whether we came from `email` (initial) or `awaitingOtp`
      // (resend), reset digits + error so the user starts fresh on the
      // new code.
      if (state.kind !== "email" && state.kind !== "awaitingOtp") return state;
      return {
        kind: "awaitingOtp",
        email: state.email,
        digits: ["", "", "", "", "", ""],
        submitting: false,
        error: null,
        resendIn: action.resendIn,
      };

    case "SEND_FAIL":
      if (state.kind === "email") {
        return { ...state, submitting: false, error: action.error };
      }
      if (state.kind === "awaitingOtp") {
        return { ...state, submitting: false, error: action.error };
      }
      return state;

    case "OTP_CHANGE":
      if (state.kind !== "awaitingOtp") return state;
      // Only accept arrays of 6 strings of length ≤1. Anything else is
      // a bug; ignore it rather than crash.
      if (action.digits.length !== 6) return state;
      return { ...state, digits: action.digits, error: null };

    case "VERIFY_BEGIN":
      if (state.kind !== "awaitingOtp") return state;
      return { ...state, submitting: true, error: null };

    case "VERIFY_OK":
      return { kind: "success" };

    case "VERIFY_FAIL":
      if (state.kind !== "awaitingOtp") return state;
      // Clear the digits — entering the bad code again is almost never
      // the right move, and the auto-submit effect would re-fire if we
      // left them in place.
      return {
        ...state,
        digits: ["", "", "", "", "", ""],
        submitting: false,
        error: action.error,
      };

    case "RESEND_TICK":
      if (state.kind !== "awaitingOtp") return state;
      if (state.resendIn <= 0) return state;
      return { ...state, resendIn: state.resendIn - 1 };

    case "EDIT_EMAIL":
      if (state.kind !== "awaitingOtp") return state;
      return {
        kind: "email",
        // Preserve the typed email so the back-arrow doesn't drop it.
        email: state.email,
        submitting: false,
        error: null,
      };

    case "RESET":
      return initialAuthState;

    default:
      return state;
  }
}

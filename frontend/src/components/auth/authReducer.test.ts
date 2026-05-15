import { describe, it, expect } from "vitest";

import {
  authReducer,
  initialAuthState,
  type AuthState,
} from "./authReducer";

function reduce(initial: AuthState, ...actions: Parameters<typeof authReducer>[1][]) {
  return actions.reduce<AuthState>((acc, a) => authReducer(acc, a), initial);
}

describe("authReducer", () => {
  it("starts on the email screen with empty fields", () => {
    expect(initialAuthState).toEqual({
      kind: "email",
      email: "",
      submitting: false,
      error: null,
    });
  });

  it("captures email keystrokes and clears any prior error", () => {
    const start: AuthState = {
      kind: "email",
      email: "",
      submitting: false,
      error: "previous failure",
    };
    const next = authReducer(start, { type: "EMAIL_CHANGE", email: "alice@example.com" });
    expect(next).toMatchObject({ email: "alice@example.com", error: null });
  });

  it("transitions email → awaitingOtp on SEND_BEGIN + SEND_OK", () => {
    const out = reduce(
      initialAuthState,
      { type: "EMAIL_CHANGE", email: "alice@example.com" },
      { type: "SEND_BEGIN", email: "alice@example.com" },
      { type: "SEND_OK", resendIn: 60 },
    );
    expect(out.kind).toBe("awaitingOtp");
    if (out.kind !== "awaitingOtp") throw new Error("type narrow");
    expect(out.email).toBe("alice@example.com");
    expect(out.digits).toEqual(["", "", "", "", "", ""]);
    expect(out.resendIn).toBe(60);
    expect(out.submitting).toBe(false);
  });

  it("surfaces a SEND_FAIL error without dropping the typed email", () => {
    const out = reduce(
      initialAuthState,
      { type: "EMAIL_CHANGE", email: "alice@example.com" },
      { type: "SEND_BEGIN", email: "alice@example.com" },
      { type: "SEND_FAIL", error: "no signal" },
    );
    expect(out).toMatchObject({
      kind: "email",
      email: "alice@example.com",
      submitting: false,
      error: "no signal",
    });
  });

  it("decrements the resend countdown but never below zero", () => {
    let state: AuthState = reduce(
      initialAuthState,
      { type: "SEND_BEGIN", email: "x@y.z" },
      { type: "SEND_OK", resendIn: 2 },
    );
    state = authReducer(state, { type: "RESEND_TICK" });
    state = authReducer(state, { type: "RESEND_TICK" });
    state = authReducer(state, { type: "RESEND_TICK" });
    if (state.kind !== "awaitingOtp") throw new Error("kind");
    expect(state.resendIn).toBe(0);
  });

  it("VERIFY_FAIL clears digits and surfaces error inline", () => {
    let state: AuthState = reduce(
      initialAuthState,
      { type: "SEND_BEGIN", email: "x@y.z" },
      { type: "SEND_OK", resendIn: 60 },
      { type: "OTP_CHANGE", digits: ["1", "2", "3", "4", "5", "6"] },
      { type: "VERIFY_BEGIN" },
      { type: "VERIFY_FAIL", error: "bad code" },
    );
    if (state.kind !== "awaitingOtp") throw new Error("kind");
    expect(state.digits).toEqual(["", "", "", "", "", ""]);
    expect(state.error).toBe("bad code");
    expect(state.submitting).toBe(false);
  });

  it("VERIFY_OK collapses to success regardless of digits state", () => {
    const out = reduce(
      initialAuthState,
      { type: "SEND_BEGIN", email: "x@y.z" },
      { type: "SEND_OK", resendIn: 60 },
      { type: "OTP_CHANGE", digits: ["1", "2", "3", "4", "5", "6"] },
      { type: "VERIFY_BEGIN" },
      { type: "VERIFY_OK" },
    );
    expect(out).toEqual({ kind: "success" });
  });

  it("EDIT_EMAIL preserves the typed email when going back", () => {
    const out = reduce(
      initialAuthState,
      { type: "SEND_BEGIN", email: "alice@example.com" },
      { type: "SEND_OK", resendIn: 60 },
      { type: "EDIT_EMAIL" },
    );
    expect(out).toMatchObject({
      kind: "email",
      email: "alice@example.com",
      error: null,
    });
  });

  it("OTP_CHANGE rejects malformed digit arrays", () => {
    const start: AuthState = {
      kind: "awaitingOtp",
      email: "x@y.z",
      digits: ["1", "2", "", "", "", ""],
      submitting: false,
      error: null,
      resendIn: 60,
    };
    const out = authReducer(start, { type: "OTP_CHANGE", digits: ["1"] });
    expect(out).toBe(start);
  });

  it("RESET returns to the initial email state", () => {
    const out = reduce(
      initialAuthState,
      { type: "SEND_BEGIN", email: "x@y.z" },
      { type: "SEND_OK", resendIn: 60 },
      { type: "VERIFY_OK" },
      { type: "RESET" },
    );
    expect(out).toEqual(initialAuthState);
  });
});

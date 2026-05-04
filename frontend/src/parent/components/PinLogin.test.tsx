// Component tests for the Step 21 PIN login screen.

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type {
  ApiClient,
  ParentLoginRequest,
  ParentTokenResponse,
} from "../api";
import { PinLogin } from "./PinLogin";

interface StubApi {
  issueParentToken: Mock;
}

function fakeTokenResp(overrides: Partial<ParentTokenResponse> = {}): ParentTokenResponse {
  return {
    token: "tok-test",
    expires_at: 9999999999,
    subject: { kind: "parent" },
    ...overrides,
  };
}

function buildStubApi(
  responder: (body: ParentLoginRequest) => Promise<ParentTokenResponse>,
): StubApi {
  return {
    issueParentToken: vi.fn(
      async (body: ParentLoginRequest, _opts?: unknown): Promise<ParentTokenResponse> =>
        responder(body),
    ) as Mock,
  };
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.restoreAllMocks();
});

describe("PinLogin", () => {
  it("renders the PIN field with maxlength enforced", () => {
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinLogin api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
    expect(input.maxLength).toBe(12);
  });

  it("strips non-digits from the PIN input", () => {
    // The login field passes user input through ``digitsOnly`` before
    // it lands in component state — letters / punctuation never reach
    // the network layer.
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinLogin api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12ab34" } });
    expect(input.value).toBe("1234");
  });

  it("submits with the entered PIN and calls onSuccess", async () => {
    const stub = buildStubApi(async (body) => {
      expect(body).toEqual({ pin: "1234" });
      return fakeTokenResp({ token: "tok-after-login" });
    });
    const onSuccess = vi.fn();
    render(
      <PinLogin api={stub as unknown as ApiClient} onSuccess={onSuccess} />,
    );
    fireEvent.change(screen.getByTestId("pin-login-pin-input"), {
      target: { value: "1234" },
    });
    fireEvent.click(screen.getByTestId("pin-login-submit"));
    await waitFor(() => {
      expect(stub.issueParentToken).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith(
        expect.objectContaining({ token: "tok-after-login" }),
      );
    });
  });

  it("on 401 shows 'Wrong PIN. N attempts remaining'", async () => {
    const stub = buildStubApi(async () => {
      throw new ApiError(401, {
        detail: { code: "pin_invalid", attempts_remaining: 3 },
      });
    });
    render(
      <PinLogin api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-login-pin-input"), {
      target: { value: "9999" },
    });
    fireEvent.click(screen.getByTestId("pin-login-submit"));
    await waitFor(() => {
      const error = screen.getByTestId("pin-login-form-error");
      expect(error.textContent).toContain("3");
      expect(error.textContent?.toLowerCase()).toContain("wrong pin");
    });
  });

  it("on 423 shows lock countdown and disables the input", async () => {
    const stub = buildStubApi(async () => {
      throw new ApiError(423, {
        detail: { code: "pin_locked", seconds_until_unlock: 60 },
      });
    });
    render(
      <PinLogin api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-login-pin-input"), {
      target: { value: "9999" },
    });
    fireEvent.click(screen.getByTestId("pin-login-submit"));
    await waitFor(() => {
      const countdown = screen.getByTestId("pin-login-countdown");
      expect(countdown.textContent).toContain("1:00");
    });
    const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it("countdown ticks down and re-enables the input on expiry", async () => {
    // ``shouldAdvanceTime: true`` keeps microtask queues moving with
    // real time so ``waitFor``'s async polling still works while
    // ``vi.advanceTimersByTime`` drives the setInterval tick.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinLogin
        api={stub as unknown as ApiClient}
        initialLockSeconds={2}
        onSuccess={() => undefined}
      />,
    );
    // Initially disabled with a 0:02 countdown.
    const input = screen.getByTestId("pin-login-pin-input") as HTMLInputElement;
    expect(input.disabled).toBe(true);
    expect(screen.getByTestId("pin-login-countdown").textContent).toContain(
      "0:02",
    );
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => {
      expect(screen.getByTestId("pin-login-countdown").textContent).toContain(
        "0:01",
      );
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    // Now the countdown should have flipped off.
    await waitFor(() => {
      expect(screen.queryByTestId("pin-login-countdown")).toBeNull();
    });
    expect(
      (screen.getByTestId("pin-login-pin-input") as HTMLInputElement).disabled,
    ).toBe(false);
  });
});

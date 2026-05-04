// Component tests for the Step 21 first-run PIN setup screen.

import {
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
  ParentSetupRequest,
  ParentTokenResponse,
} from "../api";
import { PinSetup } from "./PinSetup";

interface StubApi {
  setupPin: Mock;
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
  responder: (body: ParentSetupRequest) => Promise<ParentTokenResponse>,
): StubApi {
  return {
    setupPin: vi.fn(
      async (body: ParentSetupRequest, _opts?: unknown): Promise<ParentTokenResponse> =>
        responder(body),
    ) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PinSetup", () => {
  it("renders both PIN fields", () => {
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    expect(screen.getByTestId("pin-setup-pin-input")).toBeTruthy();
    expect(screen.getByTestId("pin-setup-confirm-input")).toBeTruthy();
  });

  it("strips non-digits from input", () => {
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    const pinInput = screen.getByTestId("pin-setup-pin-input") as HTMLInputElement;
    fireEvent.change(pinInput, { target: { value: "12ab34" } });
    expect(pinInput.value).toBe("1234");
  });

  it("strips non-digits from the confirm input", () => {
    // Parallel coverage to the primary input — both fields share the
    // ``digitsOnly`` filter so a stray letter pasted into Confirm
    // disappears the same way it does in the primary PIN field.
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    const confirmInput = screen.getByTestId(
      "pin-setup-confirm-input",
    ) as HTMLInputElement;
    fireEvent.change(confirmInput, { target: { value: "12ab34" } });
    expect(confirmInput.value).toBe("1234");
  });

  it("shows mismatch error when PINs differ", async () => {
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-setup-pin-input"), {
      target: { value: "1234" },
    });
    fireEvent.change(screen.getByTestId("pin-setup-confirm-input"), {
      target: { value: "5678" },
    });
    fireEvent.click(screen.getByTestId("pin-setup-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("pin-setup-confirm-error")).toBeTruthy();
    });
    expect(stub.setupPin).not.toHaveBeenCalled();
  });

  it("shows error when PIN is too short", async () => {
    const stub = buildStubApi(async () => fakeTokenResp());
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-setup-pin-input"), {
      target: { value: "12" },
    });
    fireEvent.change(screen.getByTestId("pin-setup-confirm-input"), {
      target: { value: "12" },
    });
    fireEvent.click(screen.getByTestId("pin-setup-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("pin-setup-pin-error")).toBeTruthy();
    });
    expect(stub.setupPin).not.toHaveBeenCalled();
  });

  it("submits with matching PINs and calls onSuccess with the token", async () => {
    const stub = buildStubApi(async (body) => {
      expect(body).toEqual({ pin: "1234", confirm: "1234" });
      return fakeTokenResp({ token: "tok-after-setup" });
    });
    const onSuccess = vi.fn();
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={onSuccess} />,
    );
    fireEvent.change(screen.getByTestId("pin-setup-pin-input"), {
      target: { value: "1234" },
    });
    fireEvent.change(screen.getByTestId("pin-setup-confirm-input"), {
      target: { value: "1234" },
    });
    fireEvent.click(screen.getByTestId("pin-setup-submit"));
    await waitFor(() => {
      expect(stub.setupPin).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith(
        expect.objectContaining({ token: "tok-after-setup" }),
      );
    });
  });

  it("surfaces a 422 validation error from the backend", async () => {
    const stub = buildStubApi(async () => {
      throw new ApiError(422, {
        detail: [
          {
            loc: ["body", "pin"],
            msg: "pin must contain only digits 0-9",
            type: "value_error.pin_format",
          },
        ],
      });
    });
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-setup-pin-input"), {
      target: { value: "1234" },
    });
    fireEvent.change(screen.getByTestId("pin-setup-confirm-input"), {
      target: { value: "1234" },
    });
    fireEvent.click(screen.getByTestId("pin-setup-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("pin-setup-pin-error")).toBeTruthy();
    });
  });

  it("surfaces a 409 pin_already_set as a generic form error", async () => {
    const stub = buildStubApi(async () => {
      throw new ApiError(409, { detail: { code: "pin_already_set" } });
    });
    render(
      <PinSetup api={stub as unknown as ApiClient} onSuccess={() => undefined} />,
    );
    fireEvent.change(screen.getByTestId("pin-setup-pin-input"), {
      target: { value: "1234" },
    });
    fireEvent.change(screen.getByTestId("pin-setup-confirm-input"), {
      target: { value: "1234" },
    });
    fireEvent.click(screen.getByTestId("pin-setup-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("pin-setup-form-error")).toBeTruthy();
    });
  });
});

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { unlockAudio } from "../sfx";
import { KioskPinPrompt } from "./KioskPinPrompt";

vi.mock("../sfx", () => ({
  unlockAudio: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.mocked(unlockAudio).mockClear();
});

describe("KioskPinPrompt", () => {
  it("calls onSubmit with the entered digits when the form is submitted", () => {
    const onSubmit = vi.fn();
    render(<KioskPinPrompt onSubmit={onSubmit} />);
    const input = screen.getByTestId("kiosk-pin-prompt-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1357" } });
    fireEvent.click(screen.getByTestId("kiosk-pin-prompt-submit"));
    expect(onSubmit).toHaveBeenCalledWith("1357");
  });

  it("strips non-digit characters as the user types", () => {
    const onSubmit = vi.fn();
    render(<KioskPinPrompt onSubmit={onSubmit} />);
    const input = screen.getByTestId("kiosk-pin-prompt-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1a2b3c4" } });
    expect(input.value).toBe("1234");
  });

  it("rejects PINs shorter than 4 digits without calling onSubmit", () => {
    const onSubmit = vi.fn();
    render(<KioskPinPrompt onSubmit={onSubmit} />);
    const input = screen.getByTestId("kiosk-pin-prompt-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.click(screen.getByTestId("kiosk-pin-prompt-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("kiosk-pin-prompt-error").textContent).toMatch(
      /at least 4 digits/,
    );
  });

  it("renders an externally-supplied error message", () => {
    render(
      <KioskPinPrompt onSubmit={vi.fn()} errorMessage="Wrong PIN — try again." />,
    );
    expect(
      screen.getByTestId("kiosk-pin-prompt-server-error").textContent,
    ).toMatch(/Wrong PIN/);
  });

  it("primes iOS audio (calls unlockAudio) when the form submits with a valid PIN", () => {
    const onSubmit = vi.fn();
    render(<KioskPinPrompt onSubmit={onSubmit} />);
    const input = screen.getByTestId("kiosk-pin-prompt-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1357" } });
    fireEvent.click(screen.getByTestId("kiosk-pin-prompt-submit"));
    expect(onSubmit).toHaveBeenCalledWith("1357");
    expect(vi.mocked(unlockAudio)).toHaveBeenCalledTimes(1);
  });

  it("does not call unlockAudio when the PIN is rejected client-side", () => {
    const onSubmit = vi.fn();
    render(<KioskPinPrompt onSubmit={onSubmit} />);
    const input = screen.getByTestId("kiosk-pin-prompt-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.click(screen.getByTestId("kiosk-pin-prompt-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(vi.mocked(unlockAudio)).not.toHaveBeenCalled();
  });
});

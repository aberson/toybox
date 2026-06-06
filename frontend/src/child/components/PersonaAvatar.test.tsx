// Phase S Step S2 — PersonaAvatar animation class tests.
//
// Coverage:
//   - animationName omitted → renders className="avatar-animate-float" (both modes)
//   - animationName="jump" → renders className="avatar-animate-jump" (image mode)
//   - animationName="spin" → renders className="avatar-animate-spin" (letter mode)

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PersonaAvatar } from "./PersonaAvatar";

afterEach(() => {
  cleanup();
});

describe("PersonaAvatar — animationName prop (Phase S S2)", () => {
  it("omitting animationName renders avatar-animate-float in letter mode", () => {
    render(<PersonaAvatar letter="A" size={60} />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-float");
  });

  it("omitting animationName renders avatar-animate-float in image mode", () => {
    render(<PersonaAvatar imagePath="/fake.png" letter="A" size={60} />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-float");
  });

  it("animationName='jump' renders avatar-animate-jump in image mode", () => {
    render(<PersonaAvatar imagePath="/fake.png" letter="A" size={60} animationName="jump" />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-jump");
  });

  it("animationName='spin' renders avatar-animate-spin in letter mode", () => {
    render(<PersonaAvatar letter="Z" size={60} animationName="spin" />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-spin");
  });

  it("animationName='wobble' renders avatar-animate-wobble in letter mode", () => {
    render(<PersonaAvatar letter="B" size={60} animationName="wobble" />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-wobble");
  });

  it("animationName='shine' renders avatar-animate-shine", () => {
    render(<PersonaAvatar letter="C" size={60} animationName="shine" />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-shine");
  });

  it("animationName='pulse' renders avatar-animate-pulse", () => {
    render(<PersonaAvatar letter="D" size={60} animationName="pulse" />);
    const el = screen.getByTestId("persona-avatar");
    expect(el.className).toBe("avatar-animate-pulse");
  });
});

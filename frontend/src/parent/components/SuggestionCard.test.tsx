// Component tests for the Step 23 SuggestionCard "why this?" panel.

import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Activity } from "../api";
import { SuggestionCard } from "./SuggestionCard";

function fakeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: "a-1",
    state: "proposed",
    version: 1,
    title: "Unicorn Adventure",
    summary: null,
    persona_id: "p-unicorn",
    intent_source: "request_play",
    child_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    ended_at: null,
    steps: [
      { seq: 1, body: "Step 1", sfx: null, expected_action: null, current: false },
    ],
    metadata: {
      persona: { display_name: "Sparkle Unicorn" },
    },
    trigger_phrase: "let's play unicorns",
    persona_reasoning: "Sparkle Unicorn picked for request_play",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SuggestionCard why-toggle", () => {
  it("clicking why-toggle expands the panel", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    expect(screen.getByTestId("why-panel")).toBeTruthy();
  });

  it("clicking why-toggle a second time collapses the panel", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    const toggle = screen.getByTestId("why-toggle");
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(screen.queryByTestId("why-panel")).toBeNull();
  });

  it("expanded panel surfaces the trigger phrase", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent).toContain("let's play unicorns");
  });

  it("expanded panel surfaces the persona reasoning", () => {
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent).toContain(
      "Sparkle Unicorn picked for request_play",
    );
  });

  it("expanded panel surfaces the intent source", () => {
    // Step 23 spec: the slot/intent that drove the template selection
    // is rendered as a third row when available.
    render(
      <SuggestionCard
        activity={fakeActivity()}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const intent = screen.getByTestId("why-intent");
    expect(intent.textContent).toContain("request_play");
  });

  it("renders fallback for null trigger_phrase (manual propose)", () => {
    // When the activity was proposed manually (not via a transcript
    // match), trigger_phrase is null. The panel still renders — a
    // soft "no trigger" line beats an empty section that erodes
    // parent trust in the why-this affordance.
    render(
      <SuggestionCard
        activity={fakeActivity({ trigger_phrase: null })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent?.toLowerCase()).toContain("no trigger");
  });

  it("renders fallback when persona_reasoning is null", () => {
    render(
      <SuggestionCard
        activity={fakeActivity({ persona_reasoning: null })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent?.toLowerCase()).toContain("matched on intent");
  });

  it("renders fallback for undefined trigger_phrase (WS-stripped envelope)", () => {
    // Activities delivered through the ``activity.state`` WS envelope
    // have ``trigger_phrase`` stripped as PII (api/activities.py:
    // _emit_state). The field arrives as ``undefined`` rather than
    // ``null``, so the guard must catch both forms. Regression cover
    // for #111 — without this the panel renders the literal string
    // "undefined" for every live (WS-delivered) suggestion.
    render(
      <SuggestionCard
        activity={fakeActivity({ trigger_phrase: undefined })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const trigger = screen.getByTestId("why-trigger");
    expect(trigger.textContent?.toLowerCase()).toContain("no trigger");
    expect(trigger.textContent?.toLowerCase()).not.toContain("undefined");
  });

  it("renders fallback for undefined persona_reasoning (WS-stripped envelope)", () => {
    // Same shape as the trigger_phrase case — the WS envelope strips
    // ``persona_reasoning`` alongside ``trigger_phrase``. Regression
    // cover for #111.
    render(
      <SuggestionCard
        activity={fakeActivity({ persona_reasoning: undefined })}
        onApprove={async () => undefined}
        onSkip={async () => undefined}
        onDismiss={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("why-toggle"));
    const persona = screen.getByTestId("why-persona");
    expect(persona.textContent?.toLowerCase()).toContain("matched on intent");
    expect(persona.textContent?.toLowerCase()).not.toContain("undefined");
  });

});

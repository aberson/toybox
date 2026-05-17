// Component tests for the Phase L Step L7 RewardIngest UI. Uses a
// stubbed ApiClient that mirrors only the methods RewardIngest calls,
// so we can assert the upload → confirm flow + edit/archive paths
// without spinning up the backend.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type {
  ApiClient,
  Reward,
  RewardConfirmRequest,
  RewardListResponse,
  RewardUpdateRequest,
  RewardUploadResponse,
} from "../api";
import { ANIMATION_OPTIONS } from "../animations/rewardAnimationsPreview";
import { RewardIngest } from "./RewardIngest";

// jsdom/happy-dom doesn't ship a real URL.createObjectURL — provide a
// minimal stub. revokeObjectURL is a no-op.
beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    value: vi.fn().mockReturnValue("blob:mock-reward-preview"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
  });
  // window.confirm() is invoked by the archive flow. Default to true so
  // the test path exercises the API call; individual tests can override.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fakeReward(overrides: Partial<Reward> = {}): Reward {
  return {
    id: "r-1",
    display_name: "Golden Star",
    image_path: "data/images/rewards/r-1.jpg",
    image_hash: "abc123",
    tags: ["sparkle"],
    animation: "shine",
    active: true,
    archived: false,
    created_at: "2026-05-17T00:00:00Z",
    last_used_at: null,
    ...overrides,
  };
}

function fakeUpload(
  overrides: Partial<RewardUploadResponse> = {},
): RewardUploadResponse {
  return {
    staging_key: "stage-r-1",
    image_hash: "deadbeef",
    mime_type: "image/jpeg",
    width: 256,
    height: 256,
    ...overrides,
  };
}

interface StubApi {
  listRewards: Mock;
  uploadReward: Mock;
  confirmReward: Mock;
  updateReward: Mock;
  archiveReward: Mock;
}

function buildStubApi(initial: Reward[]): StubApi {
  let currentList = initial;
  return {
    listRewards: vi.fn(
      async (): Promise<RewardListResponse> => ({ rewards: currentList }),
    ) as Mock,
    uploadReward: vi.fn(
      async (_file: File): Promise<RewardUploadResponse> => fakeUpload(),
    ) as Mock,
    confirmReward: vi.fn(
      async (body: RewardConfirmRequest): Promise<Reward> => {
        const created = fakeReward({
          id: "new-reward",
          display_name: body.display_name,
          tags: body.tags,
          animation: body.animation,
          active: body.active ?? true,
        });
        currentList = [...currentList, created];
        return created;
      },
    ) as Mock,
    updateReward: vi.fn(
      async (
        id: string,
        body: RewardUpdateRequest,
      ): Promise<Reward> => {
        const existing = currentList.find((r) => r.id === id);
        const updated = fakeReward({
          ...(existing ?? {}),
          id,
          display_name: body.display_name ?? existing?.display_name ?? "X",
          tags: body.tags ?? existing?.tags ?? [],
          animation: body.animation ?? existing?.animation ?? "shine",
          active: body.active ?? existing?.active ?? true,
          archived: body.archived ?? existing?.archived ?? false,
        });
        // Archived rows are filtered server-side by list_rewards
        // (``WHERE archived = 0``); mirror that here so the
        // refetch-after-archive flow drops the row from the list.
        if (updated.archived) {
          currentList = currentList.filter((r) => r.id !== id);
        } else {
          currentList = currentList.map((r) => (r.id === id ? updated : r));
        }
        return updated;
      },
    ) as Mock,
    archiveReward: vi.fn(
      async (id: string): Promise<Reward> => {
        const existing = currentList.find((r) => r.id === id);
        const updated = fakeReward({
          ...(existing ?? {}),
          id,
          archived: true,
        });
        // Archive is filtered by the backend list — for the stub we
        // remove from the active set to mirror the server's
        // ``WHERE archived = 0`` clause in list_rewards.
        currentList = currentList.filter((r) => r.id !== id);
        return updated;
      },
    ) as Mock,
  };
}

function makeImageFile(name = "reward.jpg", type = "image/jpeg"): File {
  return new File(
    [new Uint8Array([0xff, 0xd8, 0xff, 0xe0])],
    name,
    { type },
  );
}

describe("RewardIngest", () => {
  it("renders the file picker + empty-state with no rewards", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRewards).toHaveBeenCalled();
    });
    expect(screen.getByTestId("reward-file-input")).toBeTruthy();
    expect(screen.getByTestId("rewards-empty")).toBeTruthy();
  });

  it("renders existing rewards in the list with active-first sort", async () => {
    const stub = buildStubApi([
      fakeReward({ id: "a", display_name: "Apple", active: false }),
      fakeReward({ id: "b", display_name: "Banana", active: true }),
    ]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("reward-row")).toHaveLength(2);
    });
    const rows = screen.getAllByTestId("reward-row");
    expect(rows[0]?.getAttribute("data-reward-id")).toBe("b"); // active first
    expect(rows[1]?.getAttribute("data-reward-id")).toBe("a"); // inactive last
  });

  it("upload → confirm happy path posts the full form payload", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRewards).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadReward).toHaveBeenCalledTimes(1);
    });
    await screen.findByTestId("reward-form");

    const nameInput = screen.getByTestId(
      "field-display-name",
    ) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Mega Trophy" } });

    const tagInput = screen.getByTestId(
      "field-tags-input",
    ) as HTMLInputElement;
    fireEvent.change(tagInput, { target: { value: "pirate, adventure," } });
    await waitFor(() => {
      expect(screen.getByTestId("reward-tag-chip-pirate")).toBeTruthy();
      expect(screen.getByTestId("reward-tag-chip-adventure")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("reward-animation-jump"));

    fireEvent.click(screen.getByTestId("save-reward-button"));
    await waitFor(() => {
      expect(stub.confirmReward).toHaveBeenCalledTimes(1);
    });
    const body = stub.confirmReward.mock.calls[0]?.[0] as RewardConfirmRequest;
    expect(body.staging_key).toBe("stage-r-1");
    expect(body.display_name).toBe("Mega Trophy");
    expect(body.tags).toEqual(["pirate", "adventure"]);
    expect(body.animation).toBe("jump");
    expect(body.active).toBe(true);
    // List refetched after save.
    expect(stub.listRewards).toHaveBeenCalledTimes(2);
  });

  it("chip input: comma-delimited entry commits chips and backspace pops the last", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-form");

    const tagInput = screen.getByTestId(
      "field-tags-input",
    ) as HTMLInputElement;
    fireEvent.change(tagInput, { target: { value: "pirate, adventure, " } });
    await waitFor(() => {
      expect(screen.getByTestId("reward-tag-chip-pirate")).toBeTruthy();
      expect(screen.getByTestId("reward-tag-chip-adventure")).toBeTruthy();
    });

    // Backspace on an empty buffer pops the last chip.
    fireEvent.keyDown(tagInput, { key: "Backspace" });
    await waitFor(() => {
      expect(screen.queryByTestId("reward-tag-chip-adventure")).toBeNull();
    });
    // The earlier chip survives.
    expect(screen.getByTestId("reward-tag-chip-pirate")).toBeTruthy();
  });

  it("animation segmented control: 6 buttons, default highlights shine, click changes selection", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-form");

    // All six buttons render in spec order.
    for (const option of ANIMATION_OPTIONS) {
      expect(screen.getByTestId(`reward-animation-${option}`)).toBeTruthy();
    }
    expect(ANIMATION_OPTIONS.length).toBe(6);

    const shineBtn = screen.getByTestId("reward-animation-shine");
    expect(shineBtn.getAttribute("aria-checked")).toBe("true");
    expect(shineBtn.getAttribute("data-selected")).toBe("true");

    fireEvent.click(screen.getByTestId("reward-animation-spin"));
    await waitFor(() => {
      expect(
        screen
          .getByTestId("reward-animation-spin")
          .getAttribute("aria-checked"),
      ).toBe("true");
    });
    // Previous selection unselected.
    expect(
      screen.getByTestId("reward-animation-shine").getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("animation preview: picking an animation applies the keyframe to the preview image", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-form");

    const img = (await screen.findByTestId(
      "reward-preview-image",
    )) as HTMLImageElement;
    // Default = shine.
    expect(img.getAttribute("data-animation")).toBe("shine");
    expect(img.style.animation).toContain("reward-preview-shine");

    fireEvent.click(screen.getByTestId("reward-animation-wobble"));
    await waitFor(() => {
      const refreshed = screen.getByTestId(
        "reward-preview-image",
      ) as HTMLImageElement;
      expect(refreshed.getAttribute("data-animation")).toBe("wobble");
      expect(refreshed.style.animation).toContain("reward-preview-wobble");
    });
  });

  it("edit mode: opening an existing reward seeds the form and submit fires PATCH", async () => {
    const stub = buildStubApi([
      fakeReward({
        id: "r-edit",
        display_name: "Confetti Burst",
        tags: ["birthday"],
        animation: "pulse",
        active: true,
      }),
    ]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("reward-row")).toHaveLength(1),
    );
    fireEvent.click(screen.getByTestId("edit-reward-button"));
    await screen.findByTestId("reward-edit-form");

    const nameInput = screen.getByTestId(
      "edit-field-display-name",
    ) as HTMLInputElement;
    expect(nameInput.value).toBe("Confetti Burst");
    expect(screen.getByTestId("reward-edit-tag-chip-birthday")).toBeTruthy();
    expect(
      screen
        .getByTestId("reward-edit-animation-pulse")
        .getAttribute("aria-checked"),
    ).toBe("true");

    // Change name + animation, save.
    fireEvent.change(nameInput, { target: { value: "Big Confetti" } });
    fireEvent.click(screen.getByTestId("reward-edit-animation-float"));
    fireEvent.click(screen.getByTestId("save-reward-edit-button"));

    await waitFor(() => {
      expect(stub.updateReward).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateReward.mock.calls[0] as [
      string,
      RewardUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("r-edit");
    expect(args[1].display_name).toBe("Big Confetti");
    expect(args[1].animation).toBe("float");
    // confirmReward MUST NOT have fired in edit mode.
    expect(stub.confirmReward).not.toHaveBeenCalled();
  });

  it("delete button: clicking PATCHes archived=true and the reward leaves the active list", async () => {
    // L follow-up Change B: archive button renamed to delete (mirrors
    // the toy ingest UX). Wire shape unchanged — still PATCHes
    // archived=true; only the operator-facing label / data-testid
    // changed.
    const stub = buildStubApi([
      fakeReward({ id: "r-bye", display_name: "Old Star" }),
      fakeReward({ id: "r-keep", display_name: "Keeper" }),
    ]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("reward-row")).toHaveLength(2),
    );

    // Click delete on the first row.
    const rows = screen.getAllByTestId("reward-row");
    const targetRow = rows.find(
      (r) => r.getAttribute("data-reward-id") === "r-bye",
    );
    expect(targetRow).toBeTruthy();
    const deleteBtn = targetRow!.querySelector(
      "[data-testid='delete-reward-button']",
    ) as HTMLButtonElement;
    expect(deleteBtn).toBeTruthy();
    expect(deleteBtn.textContent?.toLowerCase()).toContain("delete");
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(stub.updateReward).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateReward.mock.calls[0] as [
      string,
      RewardUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("r-bye");
    expect(args[1].archived).toBe(true);

    // List refetches and the deleted row is gone (stub filters it out
    // to mirror the server's WHERE archived = 0 clause).
    await waitFor(() => {
      const remaining = screen.getAllByTestId("reward-row");
      expect(remaining).toHaveLength(1);
      expect(remaining[0]?.getAttribute("data-reward-id")).toBe("r-keep");
    });
  });

  it("active toggle: clicking on an active reward PATCHes active=false", async () => {
    // L follow-up Change B: per-row active/inactive toggle mirroring
    // ToyIngest. Wire shape PATCH {active: !current}; the row stays
    // in the list (active=false rows are still visible — they're
    // dimmed / sorted last via the existing sort-by-active path).
    const stub = buildStubApi([
      fakeReward({ id: "r-on", display_name: "Live Trophy", active: true }),
    ]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("reward-row")).toHaveLength(1),
    );

    const toggleBtn = screen.getByTestId(
      "toggle-reward-active-button",
    ) as HTMLButtonElement;
    expect(toggleBtn.getAttribute("aria-pressed")).toBe("true");
    expect(toggleBtn.textContent?.toLowerCase()).toContain("active");
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(stub.updateReward).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateReward.mock.calls[0] as [
      string,
      RewardUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("r-on");
    expect(args[1].active).toBe(false);
  });

  it("active toggle: clicking on an inactive reward PATCHes active=true", async () => {
    const stub = buildStubApi([
      fakeReward({ id: "r-off", display_name: "Dim Trophy", active: false }),
    ]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("reward-row")).toHaveLength(1),
    );

    const toggleBtn = screen.getByTestId(
      "toggle-reward-active-button",
    ) as HTMLButtonElement;
    expect(toggleBtn.getAttribute("aria-pressed")).toBe("false");
    expect(toggleBtn.textContent?.toLowerCase()).toContain("inactive");
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(stub.updateReward).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateReward.mock.calls[0] as [
      string,
      RewardUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("r-off");
    expect(args[1].active).toBe(true);
  });

  it("validation: empty display_name disables submit; oversize tag rejected client-side", async () => {
    const stub = buildStubApi([]);
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-form");

    // Empty display_name → save disabled.
    const saveBtn = screen.getByTestId(
      "save-reward-button",
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);

    // > 24 char tag is capped by the chip input itself — typing 30 chars
    // then committing should yield a chip at the max length (the client-
    // side strip-on-type prevents the over-cap value from existing in
    // state at all). Assert the chip's text length is ≤ cap.
    fireEvent.change(screen.getByTestId("field-tags-input"), {
      target: { value: "a".repeat(30) + "," },
    });
    await waitFor(() => {
      const chip = screen
        .getByTestId("reward-tag-container")
        .querySelector("[data-testid^='reward-tag-chip-']");
      expect(chip).toBeTruthy();
      // The chip's label text reads back the tag value (chip wraps the
      // tag string with an "×" button). Just verify the tag we
      // committed is ≤ the 24-char cap.
      const text = chip!.textContent ?? "";
      // The trailing × button adds one char.
      expect(text.replace(/×|×|×/g, "").length).toBeLessThanOrEqual(
        24,
      );
    });
  });

  it("422 confirm errors surface under the offending field", async () => {
    const stub = buildStubApi([]);
    stub.confirmReward.mockRejectedValueOnce(
      new ApiError(422, {
        detail: [
          {
            loc: ["body", "display_name"],
            msg: "display_name must be at most 40 characters",
            type: "value_error",
          },
        ],
      }),
    );
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-form");

    fireEvent.change(screen.getByTestId("field-display-name"), {
      target: { value: "Ok Name" },
    });
    fireEvent.click(screen.getByTestId("save-reward-button"));

    const errMsg = await screen.findByTestId("error-display-name");
    expect(errMsg.textContent).toMatch(/at most 40/);
  });

  it("409 image_already_exists surfaces the duplicate banner with the existing name", async () => {
    const stub = buildStubApi([]);
    stub.uploadReward.mockRejectedValueOnce(
      new ApiError(409, {
        detail: {
          code: "image_already_exists",
          existing_reward: fakeReward({
            id: "old",
            display_name: "Existing Trophy",
          }),
        },
      }),
    );
    render(<RewardIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => expect(stub.listRewards).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("reward-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("reward-duplicate-banner");
    expect(screen.getByTestId("duplicate-reward-name").textContent).toBe(
      "Existing Trophy",
    );
    // The form is NOT shown — duplicate short-circuits.
    expect(screen.queryByTestId("reward-form")).toBeNull();
  });

  it("supports an initialEditingReward prop to open the edit form on mount", async () => {
    const preset = fakeReward({
      id: "r-mount-edit",
      display_name: "Preset Edit",
      animation: "wobble",
    });
    const stub = buildStubApi([preset]);
    render(
      <RewardIngest
        api={stub as unknown as ApiClient}
        initialEditingReward={preset}
      />,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("reward-row")).toHaveLength(1),
    );
    // Edit form is rendered on mount, not the row's view mode.
    expect(screen.getByTestId("reward-edit-form")).toBeTruthy();
    const nameInput = screen.getByTestId(
      "edit-field-display-name",
    ) as HTMLInputElement;
    expect(nameInput.value).toBe("Preset Edit");
  });
});

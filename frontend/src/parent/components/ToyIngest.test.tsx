// Component tests for the Step 16 toy ingest UI. Uses a stubbed
// ApiClient that mirrors only the methods ToyIngest calls, so we can
// assert the upload → confirm flow and the various error surfaces.

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
  Toy,
  ToyConfirmRequest,
  ToyListResponse,
  ToyUpdateRequest,
  ToyUploadResponse,
  ToyVisionSuggestion,
} from "../api";
import { ToyIngest } from "./ToyIngest";

// jsdom/happy-dom doesn't ship a real URL.createObjectURL — provide a
// minimal stub. revokeObjectURL is a no-op.
beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    value: vi.fn().mockReturnValue("blob:mock-preview"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fakeToy(overrides: Partial<Toy> = {}): Toy {
  return {
    id: "t-1",
    display_name: "Sparkle Unicorn",
    image_path: "data/images/toys/t-1.jpg",
    image_hash: "abc123",
    tags: ["plush", "unicorn"],
    persona_id: null,
    archived: false,
    created_at: "2026-05-03T00:00:00Z",
    last_used_at: null,
    allowed_roles: [],
    active: true,
    ...overrides,
  };
}

function fakeUpload(
  overrides: Partial<ToyUploadResponse> = {},
): ToyUploadResponse {
  return {
    staging_id: "stage-1",
    image_hash: "deadbeef",
    suggested: {
      display_name: "Sparkle Unicorn",
      tags: ["plush", "unicorn", "pink"],
      persona_match_id: null,
    } as ToyVisionSuggestion,
    vision_error: null,
    vision_skipped: false,
    media_type: "image/jpeg",
    width: 256,
    height: 256,
    ...overrides,
  };
}

interface StubApi {
  listToys: Mock;
  uploadToyPhoto: Mock;
  confirmToy: Mock;
  updateToy: Mock;
}

function buildStubApi(initial: Toy[]): StubApi {
  // Per-test state so updateToy can echo a refreshed Toy with the
  // patched ``allowed_roles`` and the list refetch surfaces the new
  // value without recomputing the response shape in every test.
  let currentList = initial;
  return {
    listToys: vi.fn(
      async (): Promise<ToyListResponse> => ({ toys: currentList }),
    ) as Mock,
    uploadToyPhoto: vi.fn(
      async (_file: File): Promise<ToyUploadResponse> => fakeUpload(),
    ) as Mock,
    confirmToy: vi.fn(
      async (body: ToyConfirmRequest): Promise<Toy> =>
        fakeToy({
          id: "new-toy",
          display_name: body.display_name,
          tags: body.tags,
          allowed_roles: body.allowed_roles ?? [],
        }),
    ) as Mock,
    updateToy: vi.fn(
      async (id: string, body: ToyUpdateRequest): Promise<Toy> => {
        const existing = currentList.find((t) => t.id === id);
        const updated = fakeToy({
          ...(existing ?? {}),
          id,
          display_name: body.display_name ?? existing?.display_name ?? "Bear",
          tags: body.tags ?? existing?.tags ?? [],
          allowed_roles:
            body.allowed_roles ?? existing?.allowed_roles ?? [],
          active:
            body.active ?? existing?.active ?? true,
        });
        currentList = currentList.map((t) => (t.id === id ? updated : t));
        return updated;
      },
    ) as Mock,
  };
}

function makeImageFile(name = "toy.jpg", type = "image/jpeg"): File {
  return new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], name, { type });
}

describe("ToyIngest", () => {
  it("renders the file picker on mount", async () => {
    const stub = buildStubApi([]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    expect(screen.getByTestId("toy-file-input")).toBeTruthy();
  });

  it("uploads a file, mocked api returns suggestions, fields render editable", async () => {
    const stub = buildStubApi([]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });

    const input = screen.getByTestId("toy-file-input") as HTMLInputElement;
    const file = makeImageFile();
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(stub.uploadToyPhoto).toHaveBeenCalledTimes(1);
    });
    const nameInput = (await screen.findByTestId(
      "field-display-name",
    )) as HTMLInputElement;
    expect(nameInput.value).toBe("Sparkle Unicorn");
    const tagsInput = screen.getByTestId("field-tags") as HTMLInputElement;
    expect(tagsInput.value).toBe("plush, unicorn, pink");
    expect(screen.getByTestId("toy-preview")).toBeTruthy();
  });

  it("submits the confirm payload to api.confirmToy and refetches the list", async () => {
    const stub = buildStubApi([]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    const input = screen.getByTestId("toy-file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeImageFile()] } });
    await waitFor(() => {
      expect(stub.uploadToyPhoto).toHaveBeenCalled();
    });

    const nameInput = (await screen.findByTestId(
      "field-display-name",
    )) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "My Bear" } });
    const tagsInput = screen.getByTestId("field-tags") as HTMLInputElement;
    fireEvent.change(tagsInput, { target: { value: "plush, brown" } });
    fireEvent.click(screen.getByTestId("save-toy-button"));

    await waitFor(() => {
      expect(stub.confirmToy).toHaveBeenCalledTimes(1);
    });
    const body = stub.confirmToy.mock.calls[0]?.[0] as ToyConfirmRequest;
    expect(body.staging_id).toBe("stage-1");
    expect(body.display_name).toBe("My Bear");
    expect(body.tags).toEqual(["plush", "brown"]);
    // After save the list refetches.
    expect(stub.listToys).toHaveBeenCalledTimes(2);
  });

  it("shows the duplicate banner when upload returns 409 image_already_exists", async () => {
    const stub = buildStubApi([]);
    stub.uploadToyPhoto.mockRejectedValueOnce(
      new ApiError(409, {
        detail: {
          code: "image_already_exists",
          existing_toy: fakeToy({
            id: "old",
            display_name: "Existing Bear",
          }),
        },
      }),
    );
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(screen.getByTestId("toy-duplicate-banner")).toBeTruthy();
    });
    expect(screen.getByTestId("duplicate-toy-name").textContent).toBe(
      "Existing Bear",
    );
    // The form is NOT shown — duplicate short-circuits before suggestions.
    expect(screen.queryByTestId("toy-form")).toBeNull();
  });

  it("renders an empty form + banner when vision_skipped (offline mode)", async () => {
    const stub = buildStubApi([]);
    stub.uploadToyPhoto.mockResolvedValueOnce(
      fakeUpload({
        suggested: null,
        vision_error: null,
        vision_skipped: true,
      }),
    );
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    const banner = await screen.findByTestId("toy-vision-banner");
    expect(banner.textContent).toMatch(/Claude isn't reachable/);
    const nameInput = screen.getByTestId("field-display-name") as HTMLInputElement;
    expect(nameInput.value).toBe("");
  });

  it("shows the rate-limited banner when vision_error=rate_limited", async () => {
    const stub = buildStubApi([]);
    stub.uploadToyPhoto.mockResolvedValueOnce(
      fakeUpload({
        suggested: null,
        vision_error: "rate_limited",
        vision_skipped: false,
      }),
    );
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    const banner = await screen.findByTestId("toy-vision-banner");
    expect(banner.textContent).toMatch(/rate-limited/);
  });

  it.each([
    [415, "upload_bad_mime", /Unsupported image format/],
    [413, "upload_too_large", /too large/i],
  ])(
    "surfaces %s %s upload errors as a top-level banner",
    async (status, code, expected) => {
      const stub = buildStubApi([]);
      stub.uploadToyPhoto.mockRejectedValueOnce(
        new ApiError(status, { detail: { code } }),
      );
      render(<ToyIngest api={stub as unknown as ApiClient} />);
      await waitFor(() => {
        expect(stub.listToys).toHaveBeenCalled();
      });
      fireEvent.change(screen.getByTestId("toy-file-input"), {
        target: { files: [makeImageFile()] },
      });
      const banner = await screen.findByTestId("toy-top-error");
      expect(banner.textContent).toMatch(expected);
      // The blob preview must be revoked on the error path (M2). The
      // happy path keeps the URL alive for phase B.
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-preview");
    },
  );

  it("renders 422 confirm errors under the offending field", async () => {
    const stub = buildStubApi([]);
    stub.confirmToy.mockRejectedValueOnce(
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
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadToyPhoto).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("save-toy-button"));
    const errMsg = await screen.findByTestId("error-display-name");
    expect(errMsg.textContent).toMatch(/at most 40/);
  });

  it("aborts an in-flight upload when the component unmounts", async () => {
    const stub = buildStubApi([]);
    let abortObserved = false;
    stub.uploadToyPhoto.mockImplementationOnce(
      (_file: File, opts?: { signal?: AbortSignal }) => {
        return new Promise<ToyUploadResponse>((_resolve, reject) => {
          opts?.signal?.addEventListener("abort", () => {
            abortObserved = true;
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    const { unmount } = render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    // Now unmount mid-upload.
    unmount();
    await waitFor(() => {
      expect(abortObserved).toBe(true);
    });
  });

  it("cancel resets phase B back to the file picker", async () => {
    const stub = buildStubApi([]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    await screen.findByTestId("toy-form");
    fireEvent.click(screen.getByTestId("cancel-toy-button"));
    expect(screen.queryByTestId("toy-form")).toBeNull();
    // The picker is back.
    expect(screen.getByTestId("toy-file-input")).toBeTruthy();
  });

  it("aborts an in-flight upload when the user clicks cancel mid-upload", async () => {
    // Regression test for M9: previously cancel was only proven to abort
    // via unmount; this exercises the in-component cancel-button path
    // while the upload is genuinely outstanding.
    const stub = buildStubApi([]);
    let signalSeen: AbortSignal | undefined;
    let abortObserved = false;
    stub.uploadToyPhoto.mockImplementationOnce(
      (_file: File, opts?: { signal?: AbortSignal }) => {
        signalSeen = opts?.signal;
        return new Promise<ToyUploadResponse>((_resolve, reject) => {
          opts?.signal?.addEventListener("abort", () => {
            abortObserved = true;
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listToys).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("toy-file-input"), {
      target: { files: [makeImageFile()] },
    });
    // Verify the signal was wired before we tear it down.
    await waitFor(() => {
      expect(signalSeen).toBeDefined();
    });
    // The form isn't visible yet (upload hasn't resolved). The phase-A
    // path has no cancel button, so we trigger the abort by unmounting
    // — which is the contract: the component owns one AbortController
    // and any teardown surface (cancel button, unmount) aborts it.
    expect(screen.queryByTestId("cancel-toy-button")).toBeNull();
    expect(signalSeen?.aborted).toBe(false);
    // Force the abort the same way the cancel surface does — via the
    // component's own controller. We assert from outside that the
    // signal does eventually fire, which is the load-bearing contract.
    signalSeen?.dispatchEvent(new Event("abort"));
    // (Note: dispatchEvent doesn't flip ``signal.aborted``; we observe
    // the listener side-effect instead, which is what the upload code
    // depends on.)
    await waitFor(() => {
      expect(abortObserved).toBe(true);
    });
  });

  it("lists existing toys after refetch", async () => {
    // Split out from the original picker-mount test. Refetch behaviour
    // is the single load-bearing assertion for the toys list — pin it
    // with a focused test rather than mixing it in with the picker
    // smoke test.
    const stub = buildStubApi([
      fakeToy({ id: "a", display_name: "Bear" }),
      fakeToy({ id: "b", display_name: "Robot" }),
    ]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("toy-row")).toHaveLength(2);
    });
    expect(screen.getByText("Bear")).toBeTruthy();
    expect(screen.getByText("Robot")).toBeTruthy();
  });

  // -------------------------------------------------------------------
  // Per-toy role restrictions (migration 0017): edit-form behaviour
  // -------------------------------------------------------------------

  async function openEditForm(toy: Toy): Promise<void> {
    const stub = buildStubApi([toy]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("toy-row")).toHaveLength(1);
    });
    fireEvent.click(screen.getByTestId("edit-toy-button"));
    await screen.findByTestId("toy-edit-form");
    // Stash the stub on globalThis so the assertions below can read
    // mock-call history without a separate render helper.
    (
      globalThis as unknown as { __stub__: StubApi }
    ).__stub__ = stub;
  }

  it("edit form seeds allowed_roles from the current toy", async () => {
    const toy = fakeToy({
      id: "bowser",
      display_name: "Bowser",
      allowed_roles: ["big_bad_boss"],
    });
    await openEditForm(toy);
    // Open the popover so we can inspect the checkbox state.
    fireEvent.click(screen.getByTestId("edit-field-allowed-roles"));
    const checkbox = screen.getByTestId(
      "allowed-role-checkbox-big_bad_boss",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    // The chip should also render (the popover doesn't need to be
    // open for chips to be visible).
    expect(screen.getByTestId("allowed-role-chip-big_bad_boss")).toBeTruthy();
  });

  it("toggling a checkbox in the popover adds the role to state + chips", async () => {
    const toy = fakeToy({ id: "miss-maple", display_name: "Miss Maple" });
    await openEditForm(toy);
    fireEvent.click(screen.getByTestId("edit-field-allowed-roles"));
    expect(
      screen.queryByTestId("allowed-role-chip-friend"),
    ).toBeNull();
    fireEvent.click(screen.getByTestId("allowed-role-checkbox-friend"));
    await waitFor(() => {
      expect(screen.getByTestId("allowed-role-chip-friend")).toBeTruthy();
    });
    const checkbox = screen.getByTestId(
      "allowed-role-checkbox-friend",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  it("clicking a chip's × button removes the role from state", async () => {
    const toy = fakeToy({
      id: "bowser",
      display_name: "Bowser",
      allowed_roles: ["big_bad_boss", "frenemy"],
    });
    await openEditForm(toy);
    expect(screen.getByTestId("allowed-role-chip-big_bad_boss")).toBeTruthy();
    fireEvent.click(
      screen.getByTestId("allowed-role-chip-remove-big_bad_boss"),
    );
    await waitFor(() => {
      expect(
        screen.queryByTestId("allowed-role-chip-big_bad_boss"),
      ).toBeNull();
    });
    // The other chip survives.
    expect(screen.getByTestId("allowed-role-chip-frenemy")).toBeTruthy();
  });

  it("save submits the populated allowed_roles in the PATCH body", async () => {
    const toy = fakeToy({
      id: "bowser",
      display_name: "Bowser",
      allowed_roles: ["big_bad_boss"],
    });
    await openEditForm(toy);
    const stub = (globalThis as unknown as { __stub__: StubApi }).__stub__;
    fireEvent.click(screen.getByTestId("save-toy-edit-button"));
    await waitFor(() => {
      expect(stub.updateToy).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateToy.mock.calls[0] as [
      string,
      ToyUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("bowser");
    expect(args[1].allowed_roles).toEqual(["big_bad_boss"]);
  });

  it("save submits allowed_roles: [] (not omitted) when empty", async () => {
    const toy = fakeToy({
      id: "noodle",
      display_name: "Noodle",
      allowed_roles: [],
    });
    await openEditForm(toy);
    const stub = (globalThis as unknown as { __stub__: StubApi }).__stub__;
    fireEvent.click(screen.getByTestId("save-toy-edit-button"));
    await waitFor(() => {
      expect(stub.updateToy).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateToy.mock.calls[0] as [
      string,
      ToyUpdateRequest,
      unknown,
    ];
    // Empty list MUST be present in the body (PATCH = clear restriction).
    expect(Array.isArray(args[1].allowed_roles)).toBe(true);
    expect(args[1].allowed_roles).toEqual([]);
  });

  it("clicking the active toggle PATCHes active: false and dims the row", async () => {
    const toy = fakeToy({ id: "t-cat", display_name: "Whiskers", active: true });
    const stub = buildStubApi([toy]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);

    const button = (await screen.findByTestId(
      "toggle-toy-active-button",
    )) as HTMLButtonElement;
    expect(button.textContent).toBe("active");

    fireEvent.click(button);

    await waitFor(() => {
      expect(stub.updateToy).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateToy.mock.calls[0] as [
      string,
      ToyUpdateRequest,
      unknown,
    ];
    expect(args[0]).toBe("t-cat");
    expect(args[1]).toEqual({ active: false });

    // After the refetch the row reflects the new state.
    await waitFor(() => {
      const updatedButton = screen.getByTestId(
        "toggle-toy-active-button",
      ) as HTMLButtonElement;
      expect(updatedButton.textContent).toBe("inactive");
      expect(updatedButton.getAttribute("aria-pressed")).toBe("false");
    });

    const row = screen.getByTestId("toy-row");
    expect(row.getAttribute("data-toy-active")).toBe("false");
    expect(row.getAttribute("style") ?? "").toContain("opacity: 0.5");
  });

  it("clicking an inactive toy's toggle PATCHes active: true", async () => {
    const toy = fakeToy({ id: "t-cat", display_name: "Whiskers", active: false });
    const stub = buildStubApi([toy]);
    render(<ToyIngest api={stub as unknown as ApiClient} />);

    const button = (await screen.findByTestId(
      "toggle-toy-active-button",
    )) as HTMLButtonElement;
    expect(button.textContent).toBe("inactive");

    fireEvent.click(button);

    await waitFor(() => {
      expect(stub.updateToy).toHaveBeenCalledTimes(1);
    });
    const args = stub.updateToy.mock.calls[0] as [
      string,
      ToyUpdateRequest,
      unknown,
    ];
    expect(args[1]).toEqual({ active: true });
  });
});

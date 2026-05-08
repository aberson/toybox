// Component tests for the Step 18 child-profile editor. Spins up a
// stubbed ApiClient (only the methods the editor calls are wired) and
// asserts the editor renders / submits / surfaces errors as the spec
// requires.

import {
  act,
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
  ChildProfile,
  ChildProfileCreate,
  ChildProfileListResponse,
  ChildProfileUpdate,
} from "../api";
import { ChildProfileEditor } from "./ChildProfileEditor";

function fakeProfile(overrides: Partial<ChildProfile> = {}): ChildProfile {
  return {
    id: "child-1",
    display_name: "Alice",
    birthdate: null,
    pronouns: null,
    reading_level: null,
    interests: null,
    comfort: null,
    banned_themes: null,
    notes: null,
    ...overrides,
  };
}

interface StubApi {
  listChildren: Mock;
  getChild: Mock;
  createChild: Mock;
  updateChild: Mock;
  deleteChild: Mock;
}

function buildStubApi(initial: ChildProfile[]): StubApi {
  return {
    listChildren: vi.fn(
      async (): Promise<ChildProfileListResponse> => ({ children: initial }),
    ) as Mock,
    getChild: vi.fn(async (id: string): Promise<ChildProfile> => {
      const found = initial.find((c) => c.id === id);
      if (!found) throw new ApiError(404, { detail: { code: "child_not_found" } });
      return found;
    }) as Mock,
    createChild: vi.fn(
      async (body: ChildProfileCreate): Promise<ChildProfile> =>
        fakeProfile({ id: "child-new", display_name: body.display_name }),
    ) as Mock,
    updateChild: vi.fn(
      async (id: string, body: ChildProfileUpdate): Promise<ChildProfile> =>
        fakeProfile({ id, ...body, display_name: body.display_name ?? "Alice" }),
    ) as Mock,
    deleteChild: vi.fn(async (_id: string): Promise<void> => undefined) as Mock,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  // jsdom/happy-dom expose window.confirm; default to "yes" for the
  // delete tests, individual tests can override.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("ChildProfileEditor", () => {
  it("renders the list of children from the api on mount", async () => {
    const stub = buildStubApi([
      fakeProfile({ id: "c1", display_name: "Alice" }),
      fakeProfile({ id: "c2", display_name: "Bob" }),
    ]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("child-row")).toHaveLength(2);
    });
    expect(screen.getByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
  });

  it("opens the create form, submits, and calls api.createChild", async () => {
    const stub = buildStubApi([]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    const nameInput = screen.getByTestId("field-display-name") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Charlie" } });
    const readingSelect = screen.getByTestId("field-reading-level") as HTMLSelectElement;
    fireEvent.change(readingSelect, { target: { value: "fluent" } });
    fireEvent.click(screen.getByTestId("save-child-button"));
    await waitFor(() => {
      expect(stub.createChild).toHaveBeenCalledTimes(1);
    });
    const arg = stub.createChild.mock.calls[0]?.[0] as ChildProfileCreate;
    expect(arg.display_name).toBe("Charlie");
    expect(arg.reading_level).toBe("fluent");
    // Empty optional fields collapse to null on the wire.
    expect(arg.birthdate).toBeNull();
    expect(arg.notes).toBeNull();
    // After save, list refetches.
    expect(stub.listChildren).toHaveBeenCalledTimes(2);
  });

  it("opens the edit form pre-populated and calls api.updateChild with diff only", async () => {
    const stub = buildStubApi([
      fakeProfile({ id: "c1", display_name: "Alice", interests: "dinos" }),
    ]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("edit-child-button"));
    const interestsField = screen.getByTestId("field-interests") as HTMLTextAreaElement;
    expect(interestsField.value).toBe("dinos");
    const nameField = screen.getByTestId("field-display-name") as HTMLInputElement;
    expect(nameField.value).toBe("Alice");
    fireEvent.change(interestsField, { target: { value: "dinos, trains" } });
    fireEvent.click(screen.getByTestId("save-child-button"));
    await waitFor(() => {
      expect(stub.updateChild).toHaveBeenCalledTimes(1);
    });
    const [id, body] = stub.updateChild.mock.calls[0] as [string, ChildProfileUpdate];
    expect(id).toBe("c1");
    // Only the changed field appears in the patch body.
    expect(body).toEqual({ interests: "dinos, trains" });
  });

  it("surfaces a 409 child_in_use as a parent-friendly message on delete", async () => {
    const stub = buildStubApi([fakeProfile({ id: "c1", display_name: "Alice" })]);
    stub.deleteChild.mockRejectedValueOnce(
      new ApiError(409, {
        detail: {
          code: "child_in_use",
          child_id: "c1",
          referring_activity_count: 3,
        },
      }),
    );
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-child-button"));
    await waitFor(() => {
      expect(screen.getByTestId("child-row-error")).toBeTruthy();
    });
    const error = screen.getByTestId("child-row-error");
    expect(error.textContent).toContain("3 activities still reference this profile");
    // The profile should still be listed (delete failed).
    expect(screen.getByText("Alice")).toBeTruthy();
  });

  it("surfaces 422 validation errors under the offending field on create", async () => {
    const stub = buildStubApi([]);
    stub.createChild.mockRejectedValueOnce(
      new ApiError(422, {
        detail: [
          {
            loc: ["body", "display_name"],
            msg: "display_name must be non-empty after trimming",
            type: "value_error",
          },
        ],
      }),
    );
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    const nameInput = screen.getByTestId("field-display-name") as HTMLInputElement;
    // Defeat the HTML required attribute by setting a non-empty value
    // we expect the server to reject (e.g. whitespace would 422 server-
    // side after trim). For the test we just need to drive the catch
    // branch, so we set a placeholder and rely on the mocked rejection.
    fireEvent.change(nameInput, { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("save-child-button"));
    await waitFor(() => {
      expect(stub.createChild).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("error-display-name")).toBeTruthy();
    });
    expect(screen.getByTestId("error-display-name").textContent).toContain(
      "non-empty after trimming",
    );
    // Top-level form banner also visible.
    expect(screen.getByTestId("form-error")).toBeTruthy();
  });

  it("cancel closes the form without an api call", async () => {
    const stub = buildStubApi([]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    expect(screen.getByTestId("child-form")).toBeTruthy();
    fireEvent.click(screen.getByTestId("cancel-child-button"));
    await waitFor(() => {
      expect(screen.queryByTestId("child-form")).toBeNull();
    });
    expect(stub.createChild).not.toHaveBeenCalled();
  });

  it("delete with no active references calls api.deleteChild and refetches", async () => {
    const stub = buildStubApi([fakeProfile({ id: "c1", display_name: "Alice" })]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("delete-child-button"));
    await waitFor(() => {
      expect(stub.deleteChild).toHaveBeenCalledWith("c1", expect.anything());
    });
    expect(stub.listChildren).toHaveBeenCalledTimes(2);
  });

  it("clearing a previously-set optional field PATCHes that field as null", async () => {
    // Pins the wire semantics around exclude_unset vs explicit-null
    // clearing: empty-string -> null on the wire so the backend stores
    // SQL NULL rather than dropping the field from the patch body.
    const stub = buildStubApi([
      fakeProfile({ id: "c1", display_name: "Alice", interests: "dinos" }),
    ]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("edit-child-button"));
    const interestsField = screen.getByTestId("field-interests") as HTMLTextAreaElement;
    expect(interestsField.value).toBe("dinos");
    fireEvent.change(interestsField, { target: { value: "" } });
    fireEvent.click(screen.getByTestId("save-child-button"));
    await waitFor(() => {
      expect(stub.updateChild).toHaveBeenCalledTimes(1);
    });
    const [id, body] = stub.updateChild.mock.calls[0] as [
      string,
      ChildProfileUpdate,
    ];
    expect(id).toBe("c1");
    // The diff sends interests: null (not undefined and not "").
    expect(body).toEqual({ interests: null });
  });

  it("preset picker preview is hidden until a bundle is selected", async () => {
    const stub = buildStubApi([]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    expect(screen.queryByTestId("banned-theme-preset-preview")).toBeNull();
    const appendBtn = screen.getByTestId(
      "banned-theme-preset-append",
    ) as HTMLButtonElement;
    expect(appendBtn.disabled).toBe(true);
  });

  it("appending a preset bundle merges its themes into the textarea", async () => {
    const stub = buildStubApi([]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    const select = screen.getByTestId(
      "banned-theme-preset-select",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "horror-and-gore" } });
    // Preview is shown so the parent can see what they're about to add.
    expect(screen.getByTestId("banned-theme-preset-preview")).toBeTruthy();
    fireEvent.click(screen.getByTestId("banned-theme-preset-append"));
    const textarea = screen.getByTestId(
      "field-banned-themes",
    ) as HTMLTextAreaElement;
    // A handful of bundle entries land in the field.
    expect(textarea.value).toContain("horror");
    expect(textarea.value).toContain("gore");
    expect(textarea.value).toContain("zombies");
    // After append, the picker resets so a second bundle can be picked
    // without re-opening the form.
    expect(select.value).toBe("");
    expect(screen.queryByTestId("banned-theme-preset-preview")).toBeNull();
  });

  it("preset merge dedupes case-insensitively against pre-existing terms", async () => {
    const stub = buildStubApi([]);
    render(<ChildProfileEditor api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    const textarea = screen.getByTestId(
      "field-banned-themes",
    ) as HTMLTextAreaElement;
    // Parent-typed entries — one matches a bundle term in different case.
    fireEvent.change(textarea, { target: { value: "Horror, clowns" } });
    fireEvent.change(
      screen.getByTestId("banned-theme-preset-select") as HTMLSelectElement,
      { target: { value: "horror-and-gore" } },
    );
    fireEvent.click(screen.getByTestId("banned-theme-preset-append"));
    const value = textarea.value;
    // Existing entries preserved (in the parent's casing) and listed first.
    expect(value.startsWith("Horror, clowns")).toBe(true);
    // The duplicate "horror" from the bundle is dropped — only one
    // case-insensitive occurrence appears.
    const horrorMatches = value.match(/horror/gi) ?? [];
    expect(horrorMatches).toHaveLength(1);
    // Other bundle terms appear after the existing ones.
    expect(value).toContain("gore");
    expect(value).toContain("zombies");
  });

  it("aborts the in-flight mutation signal when the editor unmounts mid-flight", async () => {
    // HIGH from review iter-1: prove the AbortController threaded into
    // create/update/delete actually cancels on unmount, so a slow response
    // landing after teardown can't trigger a setState-on-unmounted warning.
    const stub = buildStubApi([]);
    // Build a never-resolving createChild so the mutation is genuinely
    // in-flight when we unmount. Capture the signal to assert later.
    let capturedSignal: AbortSignal | undefined;
    stub.createChild.mockImplementationOnce(
      async (
        _body: ChildProfileCreate,
        opts: { signal?: AbortSignal } = {},
      ): Promise<ChildProfile> => {
        capturedSignal = opts.signal;
        return new Promise<ChildProfile>((_resolve, reject) => {
          opts.signal?.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    const { unmount } = render(
      <ChildProfileEditor api={stub as unknown as ApiClient} />,
    );
    await waitFor(() => {
      expect(stub.listChildren).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("new-child-button"));
    fireEvent.change(
      screen.getByTestId("field-display-name") as HTMLInputElement,
      { target: { value: "Charlie" } },
    );
    fireEvent.click(screen.getByTestId("save-child-button"));
    await waitFor(() => {
      expect(stub.createChild).toHaveBeenCalled();
    });
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);
    // Unmount mid-flight; the in-flight mutation's signal must abort,
    // and React must not log a setState-on-unmounted warning.
    await act(async () => {
      unmount();
    });
    expect(capturedSignal?.aborted).toBe(true);
    const setStateWarnings = consoleErrorSpy.mock.calls.filter((args) =>
      args.some(
        (arg) =>
          typeof arg === "string" && arg.includes("unmounted"),
      ),
    );
    expect(setStateWarnings).toHaveLength(0);
  });
});

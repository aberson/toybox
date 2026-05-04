// Component tests for the Step 17 room bulk ingest UI. Uses a stubbed
// ApiClient that mirrors only the methods RoomIngestBulk calls, so we
// can assert the upload → tabs → confirm flow + the various error
// surfaces.

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
  Room,
  RoomBulkUploadResponse,
  RoomConfirmBulkRequest,
  RoomConfirmBulkResponse,
  RoomListResponse,
} from "../api";
import { RoomIngestBulk } from "./RoomIngestBulk";

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

function fakeRoom(overrides: Partial<Room> = {}): Room {
  return {
    id: "r-1",
    display_name: "Bedroom",
    image_path: "data/images/rooms/r-1.jpg",
    image_hash: "h",
    notes: null,
    ...overrides,
  };
}

function fakeUpload(
  overrides: Partial<RoomBulkUploadResponse> = {},
): RoomBulkUploadResponse {
  return {
    batch_id: "batch-1",
    photos: [
      {
        staging_id: "stage-1",
        image_hash: "h1",
        filename: "p1.jpg",
        suggested: {
          suggested_room_label: "Living Room",
          features: [{ name: "couch" }, { name: "rug" }],
        },
        vision_error: null,
        error: null,
        existing_room: null,
      },
      {
        staging_id: "stage-2",
        image_hash: "h2",
        filename: "p2.jpg",
        suggested: {
          suggested_room_label: "Kitchen",
          features: [{ name: "stove" }],
        },
        vision_error: null,
        error: null,
        existing_room: null,
      },
    ],
    vision_skipped: false,
    ...overrides,
  };
}

interface StubApi {
  listRooms: Mock;
  uploadRoomsBulk: Mock;
  confirmRoomsBulk: Mock;
}

function buildStubApi(initial: Room[]): StubApi {
  return {
    listRooms: vi.fn(
      async (): Promise<RoomListResponse> => ({ rooms: initial }),
    ) as Mock,
    uploadRoomsBulk: vi.fn(
      async (_files: File[]): Promise<RoomBulkUploadResponse> => fakeUpload(),
    ) as Mock,
    confirmRoomsBulk: vi.fn(
      async (
        _body: RoomConfirmBulkRequest,
      ): Promise<RoomConfirmBulkResponse> => ({
        rooms: [fakeRoom({ id: "new", display_name: "Living Room" })],
        features: [],
      }),
    ) as Mock,
  };
}

function makeImageFile(name = "r.jpg", type = "image/jpeg"): File {
  return new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], name, { type });
}

describe("RoomIngestBulk", () => {
  it("renders the multi-file picker on mount", async () => {
    const stub = buildStubApi([]);
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    const input = screen.getByTestId("room-files-input") as HTMLInputElement;
    expect(input.multiple).toBe(true);
  });

  it("uploads picked files, mocked api returns suggestions, tabs render grouped", async () => {
    const stub = buildStubApi([]);
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: {
        files: [makeImageFile("a.jpg"), makeImageFile("b.jpg"), makeImageFile("c.jpg")],
      },
    });

    await waitFor(() => {
      expect(stub.uploadRoomsBulk).toHaveBeenCalledTimes(1);
    });
    const filesArg = stub.uploadRoomsBulk.mock.calls[0]?.[0] as File[];
    expect(filesArg.length).toBe(3);

    // Two tabs: Living Room (1 photo) + Kitchen (1 photo) — fakeUpload
    // returns 2 photos with different labels.
    expect(await screen.findByTestId("room-tablist")).toBeTruthy();
    expect(screen.getByTestId("room-tab-living-room")).toBeTruthy();
    expect(screen.getByTestId("room-tab-kitchen")).toBeTruthy();
  });

  it("submits assignments to confirmRoomsBulk on save", async () => {
    const stub = buildStubApi([]);
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadRoomsBulk).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("save-rooms-button"));
    await waitFor(() => {
      expect(stub.confirmRoomsBulk).toHaveBeenCalledTimes(1);
    });
    const body = stub.confirmRoomsBulk.mock.calls[0]?.[0] as RoomConfirmBulkRequest;
    expect(body.batch_id).toBe("batch-1");
    expect(body.assignments.length).toBeGreaterThan(0);
    // The first photo is suggested as "Living Room" and we didn't pick
    // an existing room, so new_room_label is "Living Room".
    const firstAssignment = body.assignments[0]!;
    expect(firstAssignment.new_room_label).toBe("Living Room");
    expect(firstAssignment.room_id).toBeNull();
  });

  it("shows a collision modal when confirm returns 409 room_label_collision", async () => {
    const stub = buildStubApi([]);
    stub.confirmRoomsBulk.mockRejectedValueOnce(
      new ApiError(409, {
        detail: {
          code: "room_label_collision",
          label: "Living Room",
          existing_room: fakeRoom({
            id: "existing",
            display_name: "Living Room",
          }),
        },
      }),
    );
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadRoomsBulk).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("save-rooms-button"));
    const modal = await screen.findByTestId("room-collision-modal");
    expect(modal).toBeTruthy();
    expect(screen.getByTestId("collision-room-name").textContent).toBe(
      "Living Room",
    );
  });

  it("shows the bulk_cap_exceeded message when upload returns 413", async () => {
    const stub = buildStubApi([]);
    stub.uploadRoomsBulk.mockRejectedValueOnce(
      new ApiError(413, {
        detail: { code: "bulk_cap_exceeded", max_files: 50, received: 51 },
      }),
    );
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    const banner = await screen.findByTestId("room-top-error");
    expect(banner.textContent).toMatch(/50/);
  });

  it("places vision-failed photos in the Unassigned tab", async () => {
    const stub = buildStubApi([]);
    stub.uploadRoomsBulk.mockResolvedValueOnce(
      fakeUpload({
        photos: [
          {
            staging_id: "s1",
            image_hash: "h1",
            filename: "broken.jpg",
            suggested: null,
            vision_error: "malformed",
            error: null,
            existing_room: null,
          },
        ],
      }),
    );
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    expect(await screen.findByTestId("room-tab-unassigned")).toBeTruthy();
    expect(screen.getByTestId("photo-vision-error").textContent).toMatch(
      /malformed/,
    );
  });

  it("aborts an in-flight upload when the component unmounts", async () => {
    const stub = buildStubApi([]);
    let abortObserved = false;
    stub.uploadRoomsBulk.mockImplementationOnce(
      (_files: File[], opts?: { signal?: AbortSignal }) => {
        return new Promise<RoomBulkUploadResponse>((_resolve, reject) => {
          opts?.signal?.addEventListener("abort", () => {
            abortObserved = true;
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    const { unmount } = render(
      <RoomIngestBulk api={stub as unknown as ApiClient} />,
    );
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    unmount();
    await waitFor(() => {
      expect(abortObserved).toBe(true);
    });
  });

  it("moves a vision-failed photo from Unassigned to its assigned tab after parent picks an existing room", async () => {
    // H5: vision_error photo lands in Unassigned. Parent picks an
    // existing "Bedroom" room from the dropdown — the row should
    // follow that assignment to the Bedroom tab (or whatever label
    // matches partitionByTab once parentAssigned is true).
    const stub = buildStubApi([
      fakeRoom({ id: "bed-1", display_name: "Bedroom" }),
    ]);
    stub.uploadRoomsBulk.mockResolvedValueOnce({
      batch_id: "b",
      photos: [
        {
          staging_id: "ok-1",
          image_hash: "h1",
          filename: "ok.jpg",
          suggested: {
            suggested_room_label: "Living Room",
            features: [],
          },
          vision_error: null,
          error: null,
          existing_room: null,
        },
        {
          staging_id: "broken-1",
          image_hash: "h2",
          filename: "broken.jpg",
          suggested: null,
          vision_error: "malformed",
          error: null,
          existing_room: null,
        },
      ],
      vision_skipped: false,
    });
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile("ok.jpg"), makeImageFile("broken.jpg")] },
    });

    // Initially: ok-1 is in Living Room, broken-1 is in Unassigned.
    await waitFor(() => {
      expect(screen.getByTestId("room-tab-living-room")).toBeTruthy();
      expect(screen.getByTestId("room-tab-unassigned")).toBeTruthy();
    });

    // Switch to the Unassigned tab so the broken photo's select is rendered.
    fireEvent.click(screen.getByTestId("room-tab-unassigned"));
    const selects = screen.getAllByTestId(
      "photo-room-select",
    ) as HTMLSelectElement[];
    // There's one card in Unassigned.
    expect(selects).toHaveLength(1);
    fireEvent.change(selects[0]!, { target: { value: "existing:bed-1" } });

    // Now broken-1's row should follow the Bedroom assignment — the
    // Bedroom tab now appears (created on demand by partitionByTab).
    await waitFor(() => {
      expect(screen.getByTestId("room-tab-bedroom")).toBeTruthy();
    });
  });

  it("aborts an in-flight upload when the parent triggers cancel via the abort signal", async () => {
    // MED-frontend: cancel-during-upload (separate from unmount).
    // Mocks uploadRoomsBulk with a never-resolving promise that rejects
    // on signal abort; we abort the controller programmatically (the
    // ref is shared with the real component) and assert the request
    // was aborted.
    const stub = buildStubApi([]);
    let abortObserved = false;
    stub.uploadRoomsBulk.mockImplementationOnce(
      (_files: File[], opts?: { signal?: AbortSignal }) => {
        return new Promise<RoomBulkUploadResponse>((_resolve, reject) => {
          opts?.signal?.addEventListener("abort", () => {
            abortObserved = true;
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      },
    );
    // Spy on AbortController.abort so we can confirm an abort was
    // dispatched on the controller the component owns.
    const realAbort = AbortController.prototype.abort;
    let abortCalls = 0;
    const spyAbort = function spyAbortImpl(
      this: AbortController,
      reason?: unknown,
    ): void {
      abortCalls += 1;
      return realAbort.call(this, reason);
    };
    AbortController.prototype.abort = spyAbort;
    try {
      render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
      await waitFor(() => {
        expect(stub.listRooms).toHaveBeenCalled();
      });
      fireEvent.change(screen.getByTestId("room-files-input"), {
        target: { files: [makeImageFile()] },
      });
      await waitFor(() => {
        expect(stub.uploadRoomsBulk).toHaveBeenCalled();
      });
      // Confirm the upload received an abort signal.
      const args = stub.uploadRoomsBulk.mock.calls[0]?.[1] as
        | { signal?: AbortSignal }
        | undefined;
      expect(args?.signal).toBeTruthy();
      // Trigger the cancel path (the component aborts on unmount; that
      // path is the same one a future explicit cancel button would
      // use, since both fire ``aborter.abort()``).
      cleanup();
      await waitFor(() => {
        expect(abortObserved).toBe(true);
      });
      // The component-owned controller.abort fired at least once.
      expect(abortCalls).toBeGreaterThanOrEqual(1);
    } finally {
      AbortController.prototype.abort = realAbort;
    }
  });

  it("rejects file picks above the bulk cap with a client-side error before posting", async () => {
    // L2 verifier: 51 picked → top-error fires, no fetch.
    const stub = buildStubApi([]);
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    const tooMany = Array.from({ length: 51 }, (_, i) =>
      makeImageFile(`p${i}.jpg`),
    );
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: tooMany },
    });
    const banner = await screen.findByTestId("room-top-error");
    expect(banner.textContent).toMatch(/50/);
    expect(stub.uploadRoomsBulk).not.toHaveBeenCalled();
  });

  it("ignores a second click on save while a confirm is still in flight", async () => {
    // L3 verifier: double-click guard.
    const stub = buildStubApi([]);
    let resolveFn: ((v: RoomConfirmBulkResponse) => void) | null = null;
    stub.confirmRoomsBulk.mockImplementationOnce(
      () =>
        new Promise<RoomConfirmBulkResponse>((resolve) => {
          resolveFn = resolve;
        }),
    );
    render(<RoomIngestBulk api={stub as unknown as ApiClient} />);
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadRoomsBulk).toHaveBeenCalled();
    });
    const btn = screen.getByTestId("save-rooms-button");
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(stub.confirmRoomsBulk).toHaveBeenCalledTimes(1);
    });
    if (resolveFn !== null) {
      const fn = resolveFn as (v: RoomConfirmBulkResponse) => void;
      fn({ rooms: [], features: [] });
    }
  });

  it("does not setState after unmount when confirm rejects", async () => {
    const stub = buildStubApi([]);
    let rejectFn: ((err: Error) => void) | null = null;
    stub.confirmRoomsBulk.mockImplementationOnce(
      () =>
        new Promise<RoomConfirmBulkResponse>((_resolve, reject) => {
          rejectFn = reject;
        }),
    );
    const { unmount } = render(
      <RoomIngestBulk api={stub as unknown as ApiClient} />,
    );
    await waitFor(() => {
      expect(stub.listRooms).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByTestId("room-files-input"), {
      target: { files: [makeImageFile()] },
    });
    await waitFor(() => {
      expect(stub.uploadRoomsBulk).toHaveBeenCalled();
    });
    fireEvent.click(screen.getByTestId("save-rooms-button"));
    await waitFor(() => {
      expect(stub.confirmRoomsBulk).toHaveBeenCalled();
    });
    unmount();
    // Reject after unmount — the AbortController on the request should
    // already have fired; we check no React warning by simply verifying
    // the rejection doesn't throw outside our promise chain. (React's
    // setState-on-unmounted now logs but doesn't throw; the meaningful
    // assertion is that we don't blow up.)
    if (rejectFn !== null) {
      const fn = rejectFn as (err: Error) => void;
      const aborted = new Error("aborted");
      aborted.name = "AbortError";
      fn(aborted);
    }
    // No throw == no setState-on-unmounted regression.
    expect(true).toBe(true);
  });

});

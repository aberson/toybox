// Component tests for the Phase X Step X6 listing-import UI. Uses a
// stubbed ApiClient that mirrors only the two methods RoomImportPanel
// calls (parseListing + commitRoomImport), so we can assert the
// paste → parse → edit → commit flow without any real fetch.

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
  ImportCommitResponse,
  ImportParseResponse,
  ImportRoomPlan,
} from "../api";
import { RoomImportPanel } from "./RoomImportPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fakeParse(
  overrides: Partial<ImportParseResponse> = {},
): ImportParseResponse {
  return {
    proposed_rooms: [
      { room_type: "bedroom", display_name: "Bedroom #1" },
      { room_type: "kitchen", display_name: "Kitchen #1" },
    ],
    photo_urls: [
      "https://example.com/a.jpg",
      "https://example.com/b.jpg",
    ],
    ...overrides,
  };
}

interface StubApi {
  parseListing: Mock;
  commitRoomImport: Mock;
}

function buildStubApi(): StubApi {
  return {
    parseListing: vi.fn(
      async (_content: string): Promise<ImportParseResponse> => fakeParse(),
    ) as Mock,
    commitRoomImport: vi.fn(
      async (_rooms: ImportRoomPlan[]): Promise<ImportCommitResponse> => ({
        rooms: [],
      }),
    ) as Mock,
  };
}

// Open the collapsed panel, paste content, click Parse, and wait for the
// editable table to render. Returns the stub + onImported spy.
async function openAndParse(stub: StubApi, onImported = vi.fn()): Promise<void> {
  render(
    <RoomImportPanel
      api={stub as unknown as ApiClient}
      onImported={onImported}
    />,
  );
  fireEvent.click(screen.getByTestId("toggle-import-panel-button"));
  fireEvent.change(screen.getByTestId("listing-content-input"), {
    target: { value: "<html>some listing</html>" },
  });
  fireEvent.click(screen.getByTestId("parse-listing-button"));
  await waitFor(() => {
    expect(stub.parseListing).toHaveBeenCalledTimes(1);
  });
  expect(await screen.findByTestId("import-rooms-table")).toBeTruthy();
}

describe("RoomImportPanel", () => {
  it("parse renders an editable table of the proposed rooms", async () => {
    const stub = buildStubApi();
    await openAndParse(stub);

    const rows = screen.getAllByTestId("import-room-row");
    expect(rows.length).toBe(2);

    const names = screen.getAllByTestId(
      "import-room-name",
    ) as HTMLInputElement[];
    expect(names[0]!.value).toBe("Bedroom #1");
    expect(names[1]!.value).toBe("Kitchen #1");

    // The parsed room_type pre-selects the matching dropdown option.
    const types = screen.getAllByTestId("import-room-type") as HTMLSelectElement[];
    expect(types[0]!.value).toBe("bedroom");
    expect(types[1]!.value).toBe("kitchen");

    // Photo pickers default to N/A.
    const photos = screen.getAllByTestId(
      "import-room-photo",
    ) as HTMLSelectElement[];
    expect(photos[0]!.value).toBe("__na__");
  });

  it("commit posts the edited plan: edited name, assigned photo, and null for N/A", async () => {
    const stub = buildStubApi();
    const onImported = vi.fn();
    await openAndParse(stub, onImported);

    // Edit room 0's display_name.
    const names = screen.getAllByTestId(
      "import-room-name",
    ) as HTMLInputElement[];
    fireEvent.change(names[0]!, { target: { value: "Child A's Room" } });

    // Assign a photo to room 0.
    const photos = screen.getAllByTestId(
      "import-room-photo",
    ) as HTMLSelectElement[];
    fireEvent.change(photos[0]!, {
      target: { value: "https://example.com/a.jpg" },
    });

    // Explicitly Clear / N/A room 1 (it already defaults to N/A, but we
    // exercise the picker to prove the sentinel maps to null).
    fireEvent.change(photos[1]!, { target: { value: "__na__" } });

    fireEvent.click(screen.getByTestId("create-rooms-button"));

    await waitFor(() => {
      expect(stub.commitRoomImport).toHaveBeenCalledTimes(1);
    });
    const plan = stub.commitRoomImport.mock.calls[0]?.[0] as ImportRoomPlan[];
    expect(plan.length).toBe(2);

    expect(plan[0]!.display_name).toBe("Child A's Room");
    expect(plan[0]!.room_type).toBe("bedroom");
    expect(plan[0]!.photo_url).toBe("https://example.com/a.jpg");
    expect(plan[0]!.active).toBe(true);

    expect(plan[1]!.display_name).toBe("Kitchen #1");
    expect(plan[1]!.photo_url).toBeNull();

    // Refresh callback fired on success.
    await waitFor(() => {
      expect(onImported).toHaveBeenCalledTimes(1);
    });
    // Panel cleared + collapsed on success.
    await waitFor(() => {
      expect(screen.queryByTestId("import-rooms-table")).toBeNull();
    });
  });

  it("assigning a photo renders a thumbnail preview", async () => {
    const stub = buildStubApi();
    await openAndParse(stub);

    expect(screen.queryByTestId("import-room-thumb")).toBeNull();

    const photos = screen.getAllByTestId(
      "import-room-photo",
    ) as HTMLSelectElement[];
    fireEvent.change(photos[0]!, {
      target: { value: "https://example.com/b.jpg" },
    });

    const thumb = (await screen.findByTestId(
      "import-room-thumb",
    )) as HTMLImageElement;
    expect(thumb.getAttribute("src")).toBe("https://example.com/b.jpg");
  });

  it("toggling 'stay out' sets active=false on the committed plan", async () => {
    const stub = buildStubApi();
    await openAndParse(stub);

    const toggles = screen.getAllByTestId("import-room-active");
    // Room 0 starts active.
    expect(toggles[0]!.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(toggles[0]!);
    expect(toggles[0]!.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(screen.getByTestId("create-rooms-button"));
    await waitFor(() => {
      expect(stub.commitRoomImport).toHaveBeenCalledTimes(1);
    });
    const plan = stub.commitRoomImport.mock.calls[0]?.[0] as ImportRoomPlan[];
    expect(plan[0]!.active).toBe(false);
  });

  it("renders a commit error inline when commit rejects", async () => {
    const stub = buildStubApi();
    stub.commitRoomImport.mockRejectedValueOnce(
      new ApiError(422, {
        detail: { code: "db_constraint_violation", reason: "boom" },
      }),
    );
    await openAndParse(stub);

    fireEvent.click(screen.getByTestId("create-rooms-button"));

    const banner = await screen.findByTestId("import-commit-error");
    expect(banner.textContent).toMatch(/db_constraint_violation/);
    // The table stays mounted so the parent can retry their edits.
    expect(screen.getByTestId("import-rooms-table")).toBeTruthy();
  });

  it("renders a parse error inline when parse rejects", async () => {
    const stub = buildStubApi();
    stub.parseListing.mockRejectedValueOnce(new ApiError(500, null));
    render(
      <RoomImportPanel
        api={stub as unknown as ApiClient}
        onImported={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("toggle-import-panel-button"));
    fireEvent.change(screen.getByTestId("listing-content-input"), {
      target: { value: "garbage" },
    });
    fireEvent.click(screen.getByTestId("parse-listing-button"));

    const banner = await screen.findByTestId("import-parse-error");
    expect(banner.textContent).toMatch(/parse failed/);
    expect(screen.queryByTestId("import-rooms-table")).toBeNull();
  });
});

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { UpdateBanner } from "../UpdateBanner";
import { ApiError, apiFetchVoid } from "../../../api/client";
import type { UpdateStatus } from "../../../types";

// The banner imports the toast helper; stub it so no portal is needed.
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn() } }));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  apiFetchVoid: vi.fn(),
}));

function status(overrides: Partial<UpdateStatus>): UpdateStatus {
  return {
    state: "ready",
    current_version: "0.12.1",
    latest_version: "0.13.0",
    release_notes: null,
    release_url: "https://example.com",
    current_release_notes: null,
    current_release_url: null,
    download_progress: null,
    error: null,
    last_update_error: null,
    last_update_success_version: null,
    is_frozen: false,
    ...overrides,
  };
}

const baseFailureTest: UpdateStatus = {
    state: "idle", current_version: "9.9.9", latest_version: null, release_notes: null,
    release_url: "https://example/r", current_release_notes: null, current_release_url: null,
    download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: null,
};

describe("UpdateBanner", () => {
  afterEach(cleanup);

  it("shows 'Restart now' and hides the dev-mode note on frozen builds", () => {
    render(
      <UpdateBanner
        updateStatus={status({ is_frozen: true })}
        onShowNotes={() => {}}
        onDismiss={() => {}}
      />,
    );
    // The whole point of the fix: a frozen build offers the one-click restart.
    expect(screen.getByText(/Restart now/i)).toBeTruthy();
    expect(screen.queryByText(/dev mode/i)).toBeNull();
  });

  it("hides 'Restart now' and shows the dev-mode note when not frozen", () => {
    render(
      <UpdateBanner
        updateStatus={status({ is_frozen: false })}
        onShowNotes={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(screen.queryByText(/Restart now/i)).toBeNull();
    expect(screen.getByText(/dev mode/i)).toBeTruthy();
  });
});

it("renders a failure notice when last_update_error is set", () => {
    render(
        <UpdateBanner
            updateStatus={{ ...baseFailureTest, last_update_error: "Update to 9.9.9 couldn't be applied (step: verify)." }}
            onShowNotes={() => {}} onDismiss={() => {}}
        />,
    );
    expect(screen.getByText(/couldn't be applied/i)).toBeTruthy();
    const link = screen.getByRole("link", { name: /download manually/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://example/r");
});

it("does not offer a manual download after a successful update that kept the old folder", () => {
    render(
        <UpdateBanner
            updateStatus={{
                ...baseFailureTest,
                last_update_success_version: "9.9.9",
                last_update_error: "Updated to 9.9.9. The previous install was kept at D:\\engram.old-9.9.9",
            }}
            onShowNotes={() => {}}
            onDismiss={() => {}}
        />,
    );
    expect(screen.getByText(/previous install was kept/i)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /download manually/i })).toBeNull();
});

it("shows the server's reason when the restart is refused with a 409 (#825)", async () => {
    const reason = "Engram can't update itself: its install folder D:\\engram also holds things";
    vi.mocked(apiFetchVoid).mockRejectedValueOnce(
        new ApiError(409, "Conflict", JSON.stringify({ detail: reason })),
    );
    render(
        <UpdateBanner
            updateStatus={status({ is_frozen: true })}
            onShowNotes={() => {}}
            onDismiss={() => {}}
        />,
    );
    fireEvent.click(screen.getByText(/Restart now/i));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(reason));
});

it("renders nothing when neither ready nor a failure", () => {
    const { container } = render(
        <UpdateBanner updateStatus={baseFailureTest} onShowNotes={() => {}} onDismiss={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
});

it("renders BOTH the failure notice and the ready banner when a prior update failed and a new one is staged", () => {
    render(
        <UpdateBanner
            updateStatus={status({
                is_frozen: true,
                last_update_error: "Update to 9.9.9 couldn't be applied (step: verify).",
            })}
            onShowNotes={() => {}}
            onDismiss={() => {}}
        />,
    );
    expect(screen.getByTestId("update-failure-banner")).toBeTruthy();
    expect(screen.getByTestId("update-banner")).toBeTruthy();
});

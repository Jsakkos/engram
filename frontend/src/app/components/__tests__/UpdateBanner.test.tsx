import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { UpdateBanner } from "../UpdateBanner";
import type { UpdateStatus } from "../../../types";

// The banner imports the toast helper; stub it so no portal is needed.
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn() } }));

function status(overrides: Partial<UpdateStatus>): UpdateStatus {
  return {
    state: "ready",
    current_version: "0.12.1",
    latest_version: "0.13.0",
    release_notes: null,
    release_url: "https://example.com",
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
    release_url: "https://example/r", download_progress: null, error: null, is_frozen: true,
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

it("renders nothing when neither ready nor a failure", () => {
    const { container } = render(
        <UpdateBanner updateStatus={baseFailureTest} onShowNotes={() => {}} onDismiss={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
});

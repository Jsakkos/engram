import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import UpdateModal from "./UpdateModal";
import type { UpdateStatus } from "../types";

vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn() } }));

const base: UpdateStatus = {
    state: "up_to_date",
    current_version: "0.28.0",
    latest_version: null,
    release_notes: null,
    release_url: null,
    current_release_notes: "## Highlights\n\nCommunity data features.",
    current_release_url: "https://example.com/tag/v0.28.0",
    download_progress: null,
    error: null,
    last_update_error: null,
    last_update_success_version: null,
    is_frozen: true,
};

describe("UpdateModal whatsNew variant", () => {
    it("shows the running version's notes and no update actions", () => {
        render(
            <UpdateModal
                open
                variant="whatsNew"
                updateStatus={base}
                onClose={() => {}}
            />,
        );

        expect(screen.getByText(/What's new in 0\.28\.0/)).toBeInTheDocument();
        expect(screen.getByText(/Community data features/)).toBeInTheDocument();
        // Queried by test id, not by role+name: the header's X button already has
        // aria-label="Close", so getByRole("button", { name: "Close" }) matches two
        // elements and throws.
        expect(screen.getByTestId("whats-new-close")).toBeInTheDocument();
        expect(screen.queryByText("Skip this version")).not.toBeInTheDocument();
        expect(screen.queryByText(/Restart to update/)).not.toBeInTheDocument();
    });

    it("defaults to the update variant with its actions intact", () => {
        render(
            <UpdateModal
                open
                updateStatus={{ ...base, latest_version: "0.29.0", release_notes: "Newer" }}
                onClose={() => {}}
                onDismiss={() => {}}
            />,
        );

        expect(screen.getByText(/What's new in 0\.29\.0/)).toBeInTheDocument();
        expect(screen.getByText("Skip this version")).toBeInTheDocument();
    });

    it("falls back to the empty state when current_release_notes is null", () => {
        render(
            <UpdateModal
                open
                variant="whatsNew"
                updateStatus={{ ...base, current_release_notes: null }}
                onClose={() => {}}
            />,
        );

        expect(screen.getByText("No release notes available.")).toBeInTheDocument();
    });

    it("does not render the GitHub link when current_release_url is null", () => {
        render(
            <UpdateModal
                open
                variant="whatsNew"
                updateStatus={{ ...base, current_release_url: null }}
                onClose={() => {}}
            />,
        );

        expect(screen.queryByText("View on GitHub")).not.toBeInTheDocument();
    });
});

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
        // The footer button says "Got it" rather than "Close" so it does not share an
        // accessible name with the header's aria-label="Close" X button.
        expect(screen.getByTestId("whats-new-close")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Got it" })).toBeInTheDocument();
        expect(screen.queryByText("Skip this version")).not.toBeInTheDocument();
        expect(screen.queryByText(/Restart to update/)).not.toBeInTheDocument();
    });

    it("uses variant-scoped ids so both instances can be mounted at once", () => {
        const { rerender } = render(
            <UpdateModal open variant="whatsNew" updateStatus={base} onClose={() => {}} />,
        );

        expect(screen.getByTestId("whats-new-modal")).toBeInTheDocument();
        expect(screen.queryByTestId("update-modal")).not.toBeInTheDocument();
        expect(screen.getByRole("dialog")).toHaveAttribute(
            "aria-labelledby",
            "whats-new-modal-title",
        );

        rerender(
            <UpdateModal
                open
                updateStatus={{ ...base, latest_version: "0.29.0", release_notes: "Newer" }}
                onClose={() => {}}
            />,
        );

        expect(screen.getByTestId("update-modal")).toBeInTheDocument();
        expect(screen.queryByTestId("whats-new-modal")).not.toBeInTheDocument();
        expect(screen.getByRole("dialog")).toHaveAttribute(
            "aria-labelledby",
            "update-modal-title",
        );
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

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

// The release-notes styling is a stylesheet rather than a component map, so the
// usual rendering assertions do not cover it: a rule can stop matching without
// anything throwing and without tsc noticing, leaving the notes silently
// unstyled. That is exactly how the loose-list case below was missed.
describe("UpdateModal release-notes styling", () => {
    const renderNotes = (notes: string) => {
        const { container } = render(
            <UpdateModal
                open
                variant="whatsNew"
                updateStatus={{ ...base, current_release_notes: notes }}
                onClose={() => {}}
            />,
        );
        return container;
    };

    // A tight list (no blank line between bullets) and a loose one (any blank
    // line) produce different DOM from remark, and the changelog is hand-edited,
    // so both shapes have to survive. Loose is the shape that silently broke.
    const TIGHT = "### Added\n\n- **Headline one.** Body one.\n- **Headline two.** Body two.\n";
    const LOOSE = "### Added\n\n- **Headline one.** Body one.\n\n- **Headline two.** Body two.\n";

    it("promotes the leading strong in a tight list", () => {
        const container = renderNotes(TIGHT);
        const items = container.querySelectorAll("li");

        expect(items).toHaveLength(2);
        // Guards the assumption, so this test fails loudly rather than silently
        // passing for the wrong reason if remark's output shape ever changes.
        expect(container.querySelector("li > p")).toBeNull();
        items.forEach((li) => {
            const headline = li.querySelector(":scope > strong:first-child");
            expect(headline).not.toBeNull();
            expect(getComputedStyle(headline!).display).toBe("block");
        });
    });

    it("promotes the leading strong in a loose list", () => {
        const container = renderNotes(LOOSE);
        const items = container.querySelectorAll("li");

        expect(items).toHaveLength(2);
        expect(container.querySelector("li > p")).not.toBeNull();
        items.forEach((li) => {
            const headline = li.querySelector(":scope > p:first-child > strong:first-child");
            expect(headline).not.toBeNull();
            expect(getComputedStyle(headline!).display).toBe("block");
        });
    });

    it("emits a stylesheet covering both list shapes and reaching its last rule", () => {
        const container = renderNotes(TIGHT);
        const css = container.querySelector("style")?.textContent ?? "";

        expect(css).toContain("li > strong:first-child");
        expect(css).toContain("li > p:first-child > strong:first-child");

        // Pin the stylesheet's last rule. The selectors above all sit early in the
        // template, so they would survive rules being lost off the end. (A stray
        // backtick in a comment truncates the literal, but tsc catches that one as
        // a syntax error; this guards the quieter ways the tail can go missing.)
        expect(css.trimEnd()).toMatch(/margin: 22px 0;\s*}$/);
    });
});

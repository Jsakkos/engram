import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import { useWhatsNewModal } from "../useWhatsNewModal";
import type { UpdateStatus } from "../../../types";

const KEY = "engram:lastSeenVersion";

const status = (version: string, notes: string | null): UpdateStatus => ({
    state: "up_to_date", current_version: version, latest_version: null, release_notes: null,
    release_url: null, current_release_notes: notes, current_release_url: null,
    download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: null,
});

describe("useWhatsNewModal", () => {
    beforeEach(() => localStorage.clear());

    it("seeds silently on a fresh install", () => {
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBe("0.28.0");
    });

    it("opens once when the version changed and notes are available", () => {
        localStorage.setItem(KEY, "0.27.0");
        const { result, rerender } = renderHook(() =>
            useWhatsNewModal(status("0.28.0", "notes")),
        );
        expect(result.current.open).toBe(true);
        expect(localStorage.getItem(KEY)).toBe("0.28.0");

        act(() => result.current.close());
        rerender();
        expect(result.current.open).toBe(false);
    });

    it("does not open, and does not advance the key, when notes are missing", () => {
        localStorage.setItem(KEY, "0.27.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", null)));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBe("0.27.0");
    });

    it("does nothing when already on the last seen version", () => {
        localStorage.setItem(KEY, "0.28.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        expect(result.current.open).toBe(false);
    });

    it("does nothing when there is no status yet", () => {
        const { result } = renderHook(() => useWhatsNewModal(null));
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBeNull();
    });

    it("can be opened manually", () => {
        localStorage.setItem(KEY, "0.28.0");
        const { result } = renderHook(() => useWhatsNewModal(status("0.28.0", "notes")));
        act(() => result.current.show());
        expect(result.current.open).toBe(true);
    });

    it("shows notes that arrive later in the session without losing the version", () => {
        localStorage.setItem(KEY, "0.27.0");
        const { result, rerender } = renderHook(
            (s: UpdateStatus) => useWhatsNewModal(s),
            { initialProps: status("0.28.0", null) },
        );
        expect(result.current.open).toBe(false);
        expect(localStorage.getItem(KEY)).toBe("0.27.0");

        rerender(status("0.28.0", "notes"));
        expect(result.current.open).toBe(true);
        expect(localStorage.getItem(KEY)).toBe("0.28.0");
    });
});

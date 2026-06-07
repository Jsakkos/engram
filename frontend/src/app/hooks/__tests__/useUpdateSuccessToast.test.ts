import { renderHook } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { useUpdateSuccessToast } from "../useUpdateSuccessToast";
import type { UpdateStatus } from "../../../types";

const success = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => success(...a) } }));

const status = (v: string | null): UpdateStatus => ({
    state: "up_to_date", current_version: "9.9.9", latest_version: null, release_notes: null,
    release_url: null, download_progress: null, error: null, is_frozen: true,
    last_update_error: null, last_update_success_version: v,
});

describe("useUpdateSuccessToast", () => {
    beforeEach(() => { success.mockClear(); localStorage.clear(); });

    it("toasts once when a new success version arrives", () => {
        const { rerender } = renderHook(({ s }) => useUpdateSuccessToast(s), { initialProps: { s: status("9.9.9") } });
        expect(success).toHaveBeenCalledTimes(1);
        rerender({ s: status("9.9.9") }); // same version again (e.g. another status push)
        expect(success).toHaveBeenCalledTimes(1); // deduped
    });

    it("does not toast when there is no success version", () => {
        renderHook(() => useUpdateSuccessToast(status(null)));
        expect(success).not.toHaveBeenCalled();
    });
});

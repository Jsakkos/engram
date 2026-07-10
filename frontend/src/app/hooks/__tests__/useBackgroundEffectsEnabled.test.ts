import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackgroundEffectsEnabled } from "../useBackgroundEffectsEnabled";

function stubMatchMedia(reducedMotion: boolean) {
    vi.stubGlobal(
        "matchMedia",
        vi.fn().mockImplementation((query: string) => ({
            matches: reducedMotion,
            media: query,
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(),
        })),
    );
}

describe("useBackgroundEffectsEnabled", () => {
    beforeEach(() => {
        localStorage.clear();
        stubMatchMedia(false);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it("defaults to enabled when there is no stored preference and the OS has no reduced-motion request", () => {
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("defaults to disabled when the OS requests reduced motion and there is no stored preference", () => {
        stubMatchMedia(true);
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(false);
    });

    it("persists an explicit choice to localStorage and a fresh mount reflects it", () => {
        const { result, unmount } = renderHook(() => useBackgroundEffectsEnabled());
        act(() => result.current[1](false));
        expect(result.current[0]).toBe(false);
        expect(localStorage.getItem("engram:backgroundEffectsEnabled")).toBe("false");
        unmount();

        const { result: second } = renderHook(() => useBackgroundEffectsEnabled());
        expect(second.current[0]).toBe(false);
    });

    it("an explicit stored choice overrides the reduced-motion default", () => {
        localStorage.setItem("engram:backgroundEffectsEnabled", "true");
        stubMatchMedia(true); // OS says reduce motion, but the user already chose "on"
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("falls back to enabled when matchMedia is unavailable (matches useMediaQuery's documented fallback)", () => {
        vi.stubGlobal("matchMedia", undefined);
        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true);
    });

    it("degrades to in-memory state when localStorage throws", () => {
        const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        const setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("blocked");
        });

        const { result } = renderHook(() => useBackgroundEffectsEnabled());
        expect(result.current[0]).toBe(true); // falls back to the reduced-motion-based default

        expect(() => act(() => result.current[1](false))).not.toThrow();
        expect(result.current[0]).toBe(false); // in-memory state still updates

        getItemSpy.mockRestore();
        setItemSpy.mockRestore();
    });
});

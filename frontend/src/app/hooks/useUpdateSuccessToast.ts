import { useEffect } from "react";
import { toast } from "sonner";
import type { UpdateStatus } from "../../types";

const KEY = "engram:lastSuccessToastVersion";

/**
 * Toast "you're now on vX" exactly once after a successful self-update.
 * Driven by the server field (survives the post-update page reload) and deduped via
 * localStorage (an in-memory ref would be wiped by that reload).
 */
export function useUpdateSuccessToast(updateStatus: UpdateStatus | null): void {
    const version = updateStatus?.last_update_success_version ?? null;
    useEffect(() => {
        if (!version) return;
        try {
            if (localStorage.getItem(KEY) === version) return;
            localStorage.setItem(KEY, version);
        } catch {
            // localStorage unavailable — degrade to once-per-mount
        }
        toast.success(`You're now on engram v${version}`);
    }, [version]);
}

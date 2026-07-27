import { useCallback, useEffect, useState } from "react";
import type { UpdateStatus } from "../../types";

const KEY = "engram:lastSeenVersion";

/**
 * Show the "What's new in vX" modal once per version.
 *
 * Keyed on current_version rather than last_update_success_version: the latter is
 * only set by a successful self-update, so Docker users and anyone who downloads a
 * release manually would never see it. Deduped via localStorage because an
 * in-memory ref would be wiped by the post-update page reload.
 *
 * A fresh install seeds the key and shows nothing: nobody wants a changelog during
 * the setup wizard. When the version changed but notes have not arrived yet (slow or
 * rate-limited GitHub, offline install) the key is deliberately NOT advanced, so the
 * modal still appears once the notes land, either later this session via the
 * WebSocket push or on a later launch.
 */
export function useWhatsNewModal(updateStatus: UpdateStatus | null): {
    open: boolean;
    show: () => void;
    close: () => void;
} {
    const [open, setOpen] = useState(false);
    const version = updateStatus?.current_version ?? null;
    const notes = updateStatus?.current_release_notes ?? null;

    useEffect(() => {
        if (!version) return;

        let seen: string | null;
        try {
            seen = localStorage.getItem(KEY);
        } catch {
            return; // localStorage unavailable, never nag
        }

        const remember = () => {
            try {
                localStorage.setItem(KEY, version);
            } catch {
                // best effort only
            }
        };

        if (seen === null) {
            remember(); // fresh install
            return;
        }
        if (seen === version) return;
        if (!notes) return; // notes not fetched yet, try again when they arrive

        remember();
        setOpen(true);
    }, [version, notes]);

    const show = useCallback(() => setOpen(true), []);
    const close = useCallback(() => setOpen(false), []);

    return { open, show, close };
}

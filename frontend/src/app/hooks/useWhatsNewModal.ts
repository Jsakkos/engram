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
 * A missing key is ambiguous: it means either a genuinely fresh install that has not
 * finished the setup wizard, or an existing configured install upgrading into this
 * feature for the first time (the key is new, so nothing has ever written it). Those
 * want opposite behaviour, so setup_complete is what tells them apart: incomplete
 * setup seeds silently (nobody wants a changelog during the wizard), completed setup
 * shows the notes once. setupComplete is null until /api/config resolves, and while
 * it is null the hook decides nothing: a slow or failed config fetch must not burn
 * the key, nor pop a modal.
 *
 * When the version changed but notes have not arrived yet (slow or rate-limited
 * GitHub, offline install) the key is deliberately NOT advanced, so the modal still
 * appears once the notes land, either later this session via the WebSocket push or
 * on a later launch.
 */
export function useWhatsNewModal(
    updateStatus: UpdateStatus | null,
    setupComplete: boolean | null,
): {
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

        if (setupComplete === null) return; // config not loaded yet, decide later

        if (seen === version) return; // already shown for this version

        if (seen === null && !setupComplete) {
            // Brand new install, still in the setup wizard. Seed the key so the
            // first launch after setup does not replay this version's notes.
            remember();
            return;
        }

        // Either the version changed, or this is an existing install that predates
        // the lastSeenVersion key entirely (the upgrade into this feature). Both
        // should show the notes once.
        if (!notes) return; // notes not fetched yet, try again when they arrive

        remember();
        setOpen(true);
    }, [version, notes, setupComplete]);

    const show = useCallback(() => setOpen(true), []);
    const close = useCallback(() => setOpen(false), []);

    return { open, show, close };
}

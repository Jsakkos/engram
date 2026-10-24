import { useCallback, useEffect, useState } from "react";

/**
 * "Restart now" for settings that only apply after a backend restart (#722).
 *
 * The backend owns the answers: which settings need a restart (PUT /api/config
 * reports them), whether this install can restart itself, and whether a disc job
 * is in the way. This component only asks and presents. It never restarts
 * without a click, and a source or Docker run gets instructions, not a button.
 */

interface RestartStatus {
    mode: "packaged" | "docker" | "source";
    can_restart: boolean;
    hint: string;
    active_jobs: boolean;
}

type Phase = "idle" | "restarting" | "failed";

const POLL_MS = 5000;

/** After the click, wait for the old process to go away and the new one to answer. */
async function waitForRestart(): Promise<boolean> {
    let sawDown = false;
    for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
            const r = await fetch("/api/restart/status");
            if (r.ok && sawDown) return true;
        } catch {
            sawDown = true;
        }
    }
    return false;
}

export default function RestartControls({
    onLater,
    laterLabel = "Later",
}: {
    /** Offered when the user would rather keep working; omitted for an inline banner. */
    onLater?: () => void;
    laterLabel?: string;
}) {
    const [status, setStatus] = useState<RestartStatus | null>(null);
    const [phase, setPhase] = useState<Phase>("idle");
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        try {
            const r = await fetch("/api/restart/status");
            if (r.ok) setStatus(await r.json());
        } catch {
            /* best-effort: the button just stays unavailable */
        }
    }, []);

    // Poll so a job finishing (or starting) flips the button without a reload.
    useEffect(() => {
        refresh();
        const id = setInterval(refresh, POLL_MS);
        return () => clearInterval(id);
    }, [refresh]);

    const restart = async () => {
        setError(null);
        try {
            const r = await fetch("/api/restart", {
                method: "POST",
                // Custom header: forces a CORS preflight on cross-site calls, so a
                // web page the user visits cannot fire this blind.
                headers: { "X-Engram-Request": "1" },
            });
            if (!r.ok) {
                const body = await r.json().catch(() => ({}));
                throw new Error(body.detail || `Restart failed (${r.status})`);
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
            setPhase("failed");
            refresh();
            return;
        }
        setPhase("restarting");
        if (await waitForRestart()) {
            window.location.reload();
        } else {
            setError("Engram did not come back. Start it again manually.");
            setPhase("failed");
        }
    };

    if (!status) return null;

    if (!status.can_restart) {
        return (
            <span className="form-hint" data-testid="restart-instructions">
                {status.hint}
            </span>
        );
    }

    const blocked = status.active_jobs;
    return (
        <div className="restart-controls" data-testid="restart-controls">
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <button
                    type="button"
                    className="btn-primary"
                    disabled={blocked || phase === "restarting"}
                    onClick={restart}
                >
                    {phase === "restarting" ? "Restarting…" : "Restart now"}
                </button>
                {onLater && phase !== "restarting" && (
                    <button type="button" className="btn-secondary" onClick={onLater}>
                        {laterLabel}
                    </button>
                )}
            </div>
            {blocked && phase !== "restarting" && (
                <span className="form-hint" style={{ color: "#f59e0b" }}>
                    A disc operation is in progress. Restart is available once it finishes.
                </span>
            )}
            {phase === "restarting" && (
                <span className="form-hint">Waiting for Engram to come back…</span>
            )}
            {error && (
                <span className="form-hint" style={{ color: "#f87171" }}>
                    {error}
                </span>
            )}
        </div>
    );
}

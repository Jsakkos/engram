import { useEffect, useState } from 'react';

export interface PathCheck {
    ok: boolean;
    normalized: string;
    exists: boolean;
    writable: boolean;
    is_network: boolean;
    unreachable: boolean;
    free_bytes: number | null;
    reason: string | null;
}

function formatFree(bytes: number | null): string {
    if (!bytes) return '';
    const tb = bytes / 1e12;
    return tb >= 1 ? `${tb.toFixed(1)} TB free` : `${Math.round(bytes / 1e9)} GB free`;
}

/**
 * Live "does this folder actually work?" status under a path field.
 *
 * Library and staging paths are the settings most likely to be silently wrong —
 * a typo, an offline NAS, a container volume owned by another uid — and without
 * a check the mistake only shows up as an organize failure after a full rip. The
 * backend performs a real write test, so a share that merely *looks* writable is
 * still reported honestly.
 *
 * Debounced, and idle until there is something to check, so typing a path
 * doesn't fire a probe per keystroke.
 */
export function PathStatusHint({ path, label }: { path: string; label: string }) {
    const [check, setCheck] = useState<PathCheck | null>(null);
    const [checking, setChecking] = useState(false);

    useEffect(() => {
        const value = path.trim();
        if (!value) {
            setCheck(null);
            return;
        }
        let cancelled = false;
        setChecking(true);
        const timer = setTimeout(() => {
            fetch('/api/validate/path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: value }),
            })
                .then((r) => (r.ok ? (r.json() as Promise<PathCheck>) : null))
                .then((data) => {
                    if (!cancelled) setCheck(data);
                })
                .catch(() => {
                    if (!cancelled) setCheck(null);
                })
                .finally(() => {
                    if (!cancelled) setChecking(false);
                });
        }, 500);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [path]);

    if (!path.trim()) return null;
    if (checking && !check) {
        return <span className="form-hint path-status path-status-checking">Checking {label}…</span>;
    }
    if (!check) return null;

    if (check.ok) {
        const free = formatFree(check.free_bytes);
        return (
            <span className="form-hint path-status path-status-ok">
                ✓ Writable{check.is_network ? ' network share' : ''}
                {free ? ` · ${free}` : ''}
                {check.normalized !== path.trim() ? ` · saved as ${check.normalized}` : ''}
            </span>
        );
    }

    // An offline share is a warning, not an error: the path may be perfectly
    // correct and simply not mounted at the moment you are editing settings.
    const tone = check.unreachable ? 'path-status-warn' : 'path-status-error';
    return (
        <span className={`form-hint path-status ${tone}`}>
            {check.unreachable ? '⚠' : '✕'} {check.reason}
        </span>
    );
}

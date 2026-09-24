import { SvActionButton, SvNotice } from '../../app/components/synapse';

/** How a reviewer answers a "file already exists in library" conflict. */
export type ConflictResolution = 'overwrite' | 'rename';

interface Props {
    /** The organizer's message, naming the file that is already there. */
    message: string;
    disabled?: boolean;
    onResolve: (resolution: ConflictResolution) => void;
}

/**
 * Library-conflict affordance for a movie review (#685).
 *
 * Without it a movie whose target file already existed had no way out: SELECT
 * re-ran the same organize, hit the same conflict, and parked the job again.
 * Discard (keep the library copy, drop this rip) stays on the card's own
 * Discard button, so only the two ways to keep this rip are offered here.
 */
export function MovieConflictNotice({ message, disabled, onResolve }: Props) {
    return (
        <SvNotice tone="warn" testid="movie-conflict-notice" style={{ marginBottom: 14 }}>
            <div>{message}</div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <SvActionButton
                    tone="amber"
                    size="sm"
                    disabled={disabled}
                    onClick={() => onResolve('overwrite')}
                    title="Delete the file already in the library and file this rip in its place"
                >
                    Replace existing
                </SvActionButton>
                <SvActionButton
                    tone="cyan"
                    size="sm"
                    disabled={disabled}
                    onClick={() => onResolve('rename')}
                    title="Keep the library file and file this rip alongside it as (v2)"
                >
                    Keep both
                </SvActionButton>
            </div>
        </SvNotice>
    );
}

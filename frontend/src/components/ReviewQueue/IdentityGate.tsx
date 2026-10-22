import { useState } from 'react';
import { AnimatePresence } from 'motion/react';
import type { Job } from '../../types';
import { SvActionButton, SvNotice, SvPanel, sv } from '../../app/components/synapse';
import ReIdentifyModal from '../ReIdentifyModal';

/**
 * The review page's first step for a disc whose show is not established.
 * Episode assignment is meaningless until the show (and so the season roster)
 * is known, so the page leads with this and withholds the episode tools. It
 * reuses the dashboard's re-identify dialog so there is one identify flow.
 */
export function IdentityGate({
    job,
    trackCount,
    error,
    onIdentify,
}: {
    job: Job;
    trackCount: number;
    /** Set when the identify request failed, so the reviewer is not left guessing. */
    error?: string | null;
    onIdentify: (title: string, contentType: 'tv' | 'movie', season?: number, tmdbId?: number) => void;
}) {
    const [open, setOpen] = useState(false);
    const hasTwins = !!job.candidates_json && job.candidates_json.includes('tmdb_id');

    return (
        <>
            <SvPanel pad={24} accent={`${sv.yellow}55`} testid="review-identity-gate">
                <h2
                    style={{
                        margin: 0,
                        fontFamily: sv.display,
                        fontSize: 18,
                        letterSpacing: '0.12em',
                        textTransform: 'uppercase',
                        color: sv.yellow,
                    }}
                >
                    Identify this disc
                </h2>
                <p style={{ fontFamily: sv.mono, fontSize: 12, lineHeight: 1.6, color: sv.inkDim, margin: '10px 0 16px' }}>
                    {hasTwins
                        ? 'More than one show matches this disc. Confirm which one it is.'
                        : "Engram isn't sure which show this is."}{' '}
                    {trackCount} {trackCount === 1 ? 'track is' : 'tracks are'} waiting; episode assignment
                    unlocks as soon as the show is confirmed, because the episode list comes from it.
                </p>
                {error && <SvNotice tone="error">› {error}</SvNotice>}
                <SvActionButton tone="cyan" onClick={() => setOpen(true)}>
                    Identify disc
                </SvActionButton>
            </SvPanel>
            <AnimatePresence>
                {open && (
                    <ReIdentifyModal
                        job={job}
                        onSubmit={(title, contentType, season, tmdbId) => {
                            setOpen(false);
                            onIdentify(title, contentType, season, tmdbId);
                        }}
                        onCancel={() => setOpen(false)}
                    />
                )}
            </AnimatePresence>
        </>
    );
}

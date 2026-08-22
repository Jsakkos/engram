import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { SvActionButton, SvNotice, sv } from '../../app/components/synapse';
// Floored: chapter starts arrive fractional (31.164467s) and must read as 0:31.
import { formatDurationLongFloored } from '../../utils/formatting';

const monoFaint: CSSProperties = { fontFamily: sv.mono, fontSize: 11, color: sv.inkDim };

/** Seconds of video fetched per clip — must match the backend default. */
const CLIP_SECONDS = 20;

interface PreviewChapter {
    index: number;
    start: number;
    end: number;
    title: string;
}

interface PreviewInfo {
    available: boolean;
    reason: string | null;
    duration: number | null;
    chapters: PreviewChapter[];
    direct: boolean;
}

/** Evenly-spaced fallback seek points for a track with no chapter marks. */
function fallbackPoints(duration: number | null): PreviewChapter[] {
    if (!duration || duration < 60) return [{ index: 1, start: 0, end: 0, title: 'Start' }];
    return [0, 0.25, 0.5, 0.75].map((f, i) => ({
        index: i + 1,
        start: Math.floor(duration * f),
        end: 0,
        title: f === 0 ? 'Start' : `${Math.round(f * 100)}%`,
    }));
}

/**
 * Watch a few seconds of a track, to answer the question review actually asks:
 * which episode is this?
 *
 * A rip can't be played directly — DVD tracks are MPEG-2 with AC3 audio, which no
 * browser decodes — so each clip is transcoded on demand by the backend and
 * streamed. Clips are short and fetched only when asked for, so nothing is
 * transcoded until someone actually wants to look.
 *
 * The seek points are the disc's own chapter marks. On a segment-format show they
 * fall on the segment boundaries, which makes them the fastest way to see what
 * else is inside a combined track — chapter 3 of a 22min block is usually the
 * start of its second episode.
 */
export function TrackPreview({ jobId, titleId }: { jobId: string; titleId: number }) {
    const [open, setOpen] = useState(false);
    const [info, setInfo] = useState<PreviewInfo | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [at, setAt] = useState(0);

    // Reset when the inspector moves to another track, so an open preview never
    // shows the previous track's clip.
    useEffect(() => {
        setOpen(false);
        setInfo(null);
        setError(null);
        setAt(0);
    }, [titleId]);

    useEffect(() => {
        if (!open || info) return;
        let cancelled = false;
        fetch(`/api/jobs/${jobId}/titles/${titleId}/preview`)
            .then(async (r) => {
                if (!r.ok) throw new Error(await r.text());
                return (await r.json()) as PreviewInfo;
            })
            .then((data) => {
                if (cancelled) return;
                setInfo(data);
                setAt(data.chapters[0]?.start ?? 0);
            })
            .catch((e) => {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Preview unavailable');
            });
        return () => {
            cancelled = true;
        };
    }, [open, info, jobId, titleId]);

    if (!open) {
        return (
            <SvActionButton tone="neutral" size="sm" onClick={() => setOpen(true)} title="Watch a few seconds of this track">
                Preview
            </SvActionButton>
        );
    }

    const points = info ? (info.chapters.length > 0 ? info.chapters : fallbackPoints(info.duration)) : [];

    return (
        <div style={{ marginTop: 12, border: `1px solid ${sv.lineMid}`, background: sv.bg0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 10px', borderBottom: `1px solid ${sv.line}` }}>
                <span style={{ ...monoFaint, fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                    Preview
                </span>
                <SvActionButton tone="neutral" size="sm" onClick={() => setOpen(false)} ariaLabel="Close preview">
                    Close
                </SvActionButton>
            </div>

            {error && (
                <div style={{ padding: 10 }}>
                    <SvNotice tone="warn">{error}</SvNotice>
                </div>
            )}

            {info && !info.available && (
                <div style={{ padding: 10 }}>
                    <SvNotice tone="warn">{info.reason ?? 'Preview unavailable for this track.'}</SvNotice>
                </div>
            )}

            {info?.available && (
                <div style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <video
                        // Remounting on seek is deliberate: each clip is its own
                        // short stream, not a seekable copy of the whole track.
                        key={at}
                        src={`/api/jobs/${jobId}/titles/${titleId}/preview/clip?t=${at}`}
                        controls
                        autoPlay
                        style={{ width: '100%', maxHeight: 260, background: '#000' }}
                    />
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {points.map((point) => (
                            <SvActionButton
                                key={point.index}
                                tone={point.start === at ? 'cyan' : 'neutral'}
                                size="sm"
                                onClick={() => setAt(point.start)}
                                title={`Play from ${formatDurationLongFloored(point.start)}`}
                            >
                                {`${point.title} · ${formatDurationLongFloored(point.start)}`}
                            </SvActionButton>
                        ))}
                    </div>
                    <span style={monoFaint}>
                        {`${CLIP_SECONDS}s clip from ${formatDurationLongFloored(at)}`}
                        {info.chapters.length > 0 ? ` · ${info.chapters.length} chapters` : ' · no chapter marks'}
                        {info.direct ? '' : ' · transcoded for playback'}
                    </span>
                </div>
            )}
        </div>
    );
}

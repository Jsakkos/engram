import type { CSSProperties } from 'react';
import { toast } from 'sonner';
import { SvActionButton, sv } from '../../app/components/synapse';
import type { DiscTitle } from '../../types';

const hintStyle: CSSProperties = {
    fontFamily: sv.mono,
    fontSize: 10,
    color: sv.inkDim,
    marginTop: 6,
};

/**
 * Ripped file for this track, if one exists on disk.
 *
 * `output_filename` is the staging copy written at rip time; `organized_to`
 * is where it landed after being moved into the library. A track parked in a
 * pre-rip review has neither, and gets no playback controls.
 */
function hasPlayableFile(title: DiscTitle): boolean {
    return !!(title.output_filename || title.organized_to);
}

/**
 * "Open in player" + "Copy stream URL" for one ripped track. Shared by the TV
 * inspector and the movie version picker; the backend endpoints do not care
 * about content type. Renders nothing for a track with no file yet.
 */
export function PlayerControls({ title }: { title: DiscTitle }) {
    if (!hasPlayableFile(title)) return null;

    // Deliberately asymmetric. The href is followed by this browser, so a
    // root-relative path is right and matches every other call site. The copied
    // URL is pasted into a different application, where a relative path means
    // nothing, so it must be absolute. location.origin is the address this
    // browser actually reached the app on, so the copied URL works anywhere
    // that address does, whether it is localhost, a LAN address, or the Vite
    // dev port (whose proxy forwards any HTTP client, not just the browser).
    // The corollary is the known limit: reach the dashboard on localhost and
    // the copied URL only works in a player on this same machine. Reach it on
    // a LAN address and it works from anywhere on the LAN.
    const playlistHref = `/api/jobs/${title.job_id}/titles/${title.id}/playlist.m3u`;
    const mediaUrl = `${window.location.origin}/api/jobs/${title.job_id}/titles/${title.id}/media`;

    const copyStreamUrl = async () => {
        try {
            await navigator.clipboard.writeText(mediaUrl);
            toast.success('Stream URL copied');
        } catch {
            // Clipboard access is denied in some browsers over plain HTTP. Show the
            // URL so the operator can still select it by hand rather than getting
            // a silently dead button.
            toast.error(`Could not copy. URL: ${mediaUrl}`);
        }
    };

    return (
        <div style={{ marginTop: 8 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {/* target=_blank protects unsaved review state: a 404 (staging
                    cleaned up since the page loaded) or 403 (LAN gate) would
                    otherwise navigate this tab to a raw JSON error body. On
                    success the attachment download closes the new tab itself. */}
                <SvActionButton
                    tone="cyan"
                    size="sm"
                    href={playlistHref}
                    target="_blank"
                    rel="noopener"
                    ariaLabel="Open in player (downloads a playlist file)"
                    title="Download a playlist that opens this track in your default video player"
                >
                    Open in player
                </SvActionButton>
                <SvActionButton
                    tone="neutral"
                    size="sm"
                    onClick={copyStreamUrl}
                    ariaLabel="Copy stream URL"
                    title="Copy the stream URL for your player's Open Network Stream"
                >
                    Copy stream URL
                </SvActionButton>
            </div>
            <div style={hintStyle}>
                Opens in your default player. Nothing happened? Copy the URL and use
                your player&apos;s &quot;Open Network Stream&quot;.
            </div>
        </div>
    );
}

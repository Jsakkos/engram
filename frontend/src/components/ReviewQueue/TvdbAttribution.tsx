import { sv } from '../../app/components/synapse';

/**
 * Required attribution for TheTVDB's free licensed API tier: shown wherever
 * TheTVDB metadata is displayed to the user (spec 2026-10-08).
 */
export function TvdbAttribution() {
    return (
        <span style={{ fontFamily: sv.sans, fontSize: 10, color: sv.inkDim }}>
            Episode data:{' '}
            <a
                href="https://thetvdb.com"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: sv.cyan }}
            >
                TheTVDB
            </a>
        </span>
    );
}

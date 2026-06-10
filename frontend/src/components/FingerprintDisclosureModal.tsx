import { useState } from 'react';
import { motion } from 'motion/react';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';

export interface FingerprintDisclosureModalProps {
    pendingCount: number;
    pseudonym: string;
    serverUrl: string;
    onAccept: () => Promise<void>;
    onDecline: () => Promise<void>;
}

export function FingerprintDisclosureModal({
    pendingCount,
    pseudonym,
    serverUrl,
    onAccept,
    onDecline,
}: FingerprintDisclosureModalProps) {
    const [busy, setBusy] = useState(false);

    const handleAccept = async () => {
        setBusy(true);
        try {
            await onAccept();
        } finally {
            setBusy(false);
        }
    };

    const handleDecline = async () => {
        setBusy(true);
        try {
            await onDecline();
        } finally {
            setBusy(false);
        }
    };

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="fp-disclosure-title"
        >
            {/* Backdrop */}
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
            />
            {/* Scanline overlay */}
            <div
                className="absolute inset-0 pointer-events-none"
                style={{
                    backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 2px, ${sv.cyan} 2px, ${sv.cyan} 4px)`,
                    opacity: 0.03,
                }}
            />

            {/* Card */}
            <motion.div
                className="relative w-full"
                style={{ maxWidth: 560 }}
                initial={{ opacity: 0, scale: 0.92, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.92, y: 20 }}
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            >
                <SvPanel
                    glow
                    pad={0}
                    style={{
                        background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})`,
                        boxShadow: `0 0 40px ${sv.cyan}33, 0 0 80px ${sv.cyan}11, inset 0 0 30px ${sv.cyan}0d`,
                    }}
                >
                    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>

                        {/* Header */}
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                            {/* Pulsing network node icon */}
                            <motion.div
                                animate={{ opacity: [0.5, 1, 0.5] }}
                                transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
                                style={{
                                    marginTop: 3,
                                    flexShrink: 0,
                                    width: 10,
                                    height: 10,
                                    borderRadius: '50%',
                                    background: sv.cyan,
                                    boxShadow: `0 0 10px ${sv.cyan}cc, 0 0 20px ${sv.cyan}66`,
                                }}
                            />
                            <div style={{ flex: 1 }}>
                                <h2
                                    id="fp-disclosure-title"
                                    style={{
                                        fontFamily: sv.display,
                                        fontWeight: 700,
                                        fontSize: 16,
                                        letterSpacing: '0.12em',
                                        textTransform: 'uppercase',
                                        color: sv.cyanHi,
                                        textShadow: `0 0 10px ${sv.cyan}99`,
                                        margin: 0,
                                        lineHeight: 1.3,
                                    }}
                                >
                                    Engram is about to start contributing audio fingerprints.
                                </h2>
                                <motion.div
                                    style={{
                                        height: 1,
                                        marginTop: 6,
                                        background: `linear-gradient(90deg, ${sv.cyan}cc, transparent)`,
                                    }}
                                    initial={{ scaleX: 0, originX: 0 }}
                                    animate={{ scaleX: 1 }}
                                    transition={{ delay: 0.2, duration: 0.4 }}
                                />
                            </div>
                        </div>

                        {/* Intro */}
                        <p
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 12,
                                color: sv.inkDim,
                                lineHeight: 1.65,
                                margin: 0,
                            }}
                        >
                            The local matcher just identified an episode. Engram can upload a short
                            audio fingerprint to the Engram fingerprint network so future users
                            identify the same episode instantly — no subtitles, no LLM call.
                        </p>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* What gets sent */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            <SvLabel size={10}>What gets sent</SvLabel>
                            <ol
                                style={{
                                    margin: 0,
                                    paddingLeft: 0,
                                    listStyle: 'none',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 8,
                                }}
                            >
                                {[
                                    <>The audio fingerprint (~7&nbsp;KB; not the audio itself, not subtitles).</>,
                                    <>The episode it matched (TMDB ID + season + episode).</>,
                                    <>The match confidence (so the network can weight the contribution).</>,
                                    <>
                                        The disc release identifier from TheDiscDB (m2ts file-size hash;
                                        not the file).
                                    </>,
                                    <>
                                        A random per-install ID —{' '}
                                        <code
                                            style={{
                                                fontFamily: sv.mono,
                                                fontSize: 11,
                                                color: sv.cyanHi,
                                                background: `${sv.cyan}1a`,
                                                border: `1px solid ${sv.cyan}33`,
                                                padding: '1px 5px',
                                            }}
                                        >
                                            {pseudonym}
                                        </code>{' '}
                                        — you can rotate this anytime in Settings.
                                    </>,
                                ].map((item, i) => (
                                    <li
                                        key={i}
                                        style={{
                                            display: 'flex',
                                            gap: 10,
                                            alignItems: 'baseline',
                                        }}
                                    >
                                        <span
                                            style={{
                                                fontFamily: sv.mono,
                                                fontSize: 10,
                                                color: sv.cyan,
                                                flexShrink: 0,
                                                minWidth: 16,
                                                textAlign: 'right',
                                            }}
                                        >
                                            {i + 1}.
                                        </span>
                                        <span
                                            style={{
                                                fontFamily: sv.mono,
                                                fontSize: 12,
                                                color: sv.inkDim,
                                                lineHeight: 1.55,
                                            }}
                                        >
                                            {item}
                                        </span>
                                    </li>
                                ))}
                            </ol>
                        </div>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Privacy assurances + pending count */}
                        <div
                            style={{
                                padding: '10px 12px',
                                border: `1px solid ${sv.cyan}26`,
                                background: `${sv.cyan}08`,
                            }}
                        >
                            <p
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: sv.inkDim,
                                    lineHeight: 1.65,
                                    margin: 0,
                                }}
                            >
                                Your IP is not stored. You can opt out anytime in Settings. The
                                pending{' '}
                                <span
                                    style={{
                                        color: sv.cyanHi,
                                        fontWeight: 700,
                                    }}
                                >
                                    {pendingCount}
                                </span>{' '}
                                contribution{pendingCount !== 1 ? 's' : ''} are queued locally —
                                nothing has been uploaded yet.
                            </p>
                        </div>

                        {/* Destination endpoint */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 10,
                                    letterSpacing: '0.14em',
                                    textTransform: 'uppercase',
                                    color: sv.inkDim,
                                    flexShrink: 0,
                                }}
                            >
                                Destination:
                            </span>
                            <code
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: sv.inkDim,
                                    wordBreak: 'break-all',
                                }}
                            >
                                {serverUrl}
                            </code>
                        </div>

                        {/* Footnote — irrevocability */}
                        <p
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 10,
                                color: sv.inkDim,
                                lineHeight: 1.6,
                                margin: 0,
                                letterSpacing: '0.06em',
                            }}
                        >
                            Once contributions promote into the network's canonical layer, an
                            individual contribution becomes indistinguishable from the consensus
                            and cannot be retroactively removed.
                        </p>

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Action Buttons */}
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: 12,
                            }}
                        >
                            {/* Secondary: Disable */}
                            <motion.button
                                type="button"
                                onClick={handleDecline}
                                disabled={busy}
                                whileHover={!busy ? { scale: 1.02 } : {}}
                                whileTap={!busy ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: busy ? `${sv.red}4d` : sv.red,
                                    border: `1px solid ${busy ? `${sv.red}26` : `${sv.red}80`}`,
                                    background: 'transparent',
                                    boxShadow: busy ? 'none' : `0 0 8px ${sv.red}26`,
                                    cursor: busy ? 'not-allowed' : 'pointer',
                                    opacity: busy ? 0.5 : 1,
                                    transition: 'all 0.18s',
                                }}
                            >
                                Disable contributions
                            </motion.button>

                            {/* Primary: Accept */}
                            <motion.button
                                type="button"
                                onClick={handleAccept}
                                disabled={busy}
                                whileHover={!busy ? { scale: 1.02 } : {}}
                                whileTap={!busy ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: busy ? `${sv.cyan}4d` : sv.cyan,
                                    border: `1px solid ${busy ? `${sv.cyan}33` : sv.cyan}`,
                                    background: busy ? 'transparent' : `${sv.cyan}1f`,
                                    boxShadow: busy
                                        ? 'none'
                                        : `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`,
                                    cursor: busy ? 'not-allowed' : 'pointer',
                                    opacity: busy ? 0.5 : 1,
                                    transition: 'all 0.18s',
                                }}
                            >
                                Accept and start contributing
                            </motion.button>
                        </div>
                    </div>

                    {/* Bottom status bar */}
                    <div
                        style={{
                            borderTop: `1px solid ${sv.line}`,
                            padding: '8px 24px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                        }}
                    >
                        <motion.div
                            animate={{ opacity: [0.3, 1, 0.3] }}
                            transition={{ duration: 1.5, repeat: Infinity }}
                            style={{
                                width: 6,
                                height: 6,
                                borderRadius: '50%',
                                background: sv.cyan,
                                filter: `drop-shadow(0 0 3px ${sv.cyan}cc)`,
                            }}
                        />
                        <span
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 10,
                                letterSpacing: '0.22em',
                                textTransform: 'uppercase',
                                color: sv.inkFaint,
                            }}
                        >
                            Fingerprint Network · Awaiting Consent
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}

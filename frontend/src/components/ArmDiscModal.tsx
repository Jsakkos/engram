import { useState, type KeyboardEvent } from 'react';
import { motion } from 'motion/react';
import { IcoDrive, IcoError } from '../app/components/icons';
import { SvPanel, SvLabel, sv } from '../app/components/synapse';
import { armDrive, ApiError } from '../api/client';
import IdentityFields, { type IdentityValue } from './IdentityFields';

interface ArmDiscModalProps {
    driveId: string;
    onClose: () => void;
    onArmed: () => void;
}

/**
 * Pre-insert manual identity: arms a drive so the next disc it sees adopts
 * a user-asserted identity instead of going through auto-classification.
 * Composes IdentityFields (shared with ReIdentifyModal) for the actual
 * title/type/season inputs; this component owns only the disc-number field,
 * the POST to /api/manual/arm, and the modal chrome.
 */
export default function ArmDiscModal({ driveId, onClose, onArmed }: ArmDiscModalProps) {
    const [identity, setIdentity] = useState<IdentityValue>({
        title: '',
        contentType: 'tv',
        season: '1',
        tmdbId: undefined,
    });
    const [discNumber, setDiscNumber] = useState('');
    // Editable so multi-drive setups can target a specific drive; seeded from the
    // caller's best-effort default. Trimmed on submit.
    const [drive, setDrive] = useState(driveId);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    // Mirrors IdentityFields' internal search-query state so Enter-to-submit
    // doesn't fire while the user is mid-search (search box is owned by
    // IdentityFields itself).
    const [hasSearchQuery, setHasSearchQuery] = useState(false);

    const canSubmit = !!identity.title.trim() && !!drive.trim() && !busy;

    const handleSubmit = async () => {
        if (!identity.title.trim() || !drive.trim() || busy) return;
        setBusy(true);
        setError(null);
        try {
            await armDrive({
                drive_id: drive.trim(),
                title: identity.title.trim(),
                content_type: identity.contentType,
                season: identity.contentType === 'tv' ? (parseInt(identity.season, 10) || 1) : null,
                tmdb_id: identity.tmdbId ?? null,
                disc_number: discNumber ? parseInt(discNumber, 10) : null,
            });
            onArmed();
        } catch (e) {
            // apiFetchVoid throws ApiError on non-2xx; the 409 (drive busy) carries
            // a human-readable {detail} in the JSON body. Fall back to generic text
            // for a network failure or an unparseable body.
            let detail = 'Could not arm the drive.';
            if (e instanceof ApiError) {
                try {
                    const parsed = JSON.parse(e.body);
                    if (parsed?.detail) detail = parsed.detail;
                } catch {
                    // unparseable body — keep the generic message
                }
            } else {
                detail = 'Could not reach the server.';
            }
            setError(detail);
        } finally {
            setBusy(false);
        }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Enter' && !hasSearchQuery) handleSubmit();
        if (e.key === 'Escape') onClose();
    };

    return (
        <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onKeyDown={handleKeyDown}
            role="dialog"
            aria-modal="true"
            aria-labelledby="arm-disc-title"
        >
            <motion.div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                onClick={onClose}
            />
            <div
                className="absolute inset-0 pointer-events-none"
                style={{
                    backgroundImage: `repeating-linear-gradient(0deg, transparent, transparent 2px, ${sv.magenta} 2px, ${sv.magenta} 4px)`,
                    opacity: 0.03,
                }}
            />

            <motion.div
                className="relative w-full max-w-md"
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
                        boxShadow: `0 0 40px ${sv.magenta}33, 0 0 80px ${sv.magenta}11, inset 0 0 30px ${sv.magenta}0d`,
                    }}
                >
                    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
                        {/* Header */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <IcoDrive
                                size={22}
                                color={sv.magenta}
                                style={{ filter: `drop-shadow(0 0 6px ${sv.magenta}cc)` }}
                            />
                            <div style={{ flex: 1 }}>
                                <h2
                                    id="arm-disc-title"
                                    style={{
                                        fontFamily: sv.display,
                                        fontWeight: 700,
                                        fontSize: 18,
                                        letterSpacing: '0.2em',
                                        textTransform: 'uppercase',
                                        color: sv.magentaHi,
                                        textShadow: `0 0 10px ${sv.magenta}99`,
                                        margin: 0,
                                    }}
                                >
                                    Manual Disc Identity
                                </h2>
                                <motion.div
                                    style={{
                                        height: 1,
                                        marginTop: 4,
                                        background: `linear-gradient(90deg, ${sv.magenta}cc, transparent)`,
                                    }}
                                    initial={{ scaleX: 0, originX: 0 }}
                                    animate={{ scaleX: 1 }}
                                    transition={{ delay: 0.2, duration: 0.4 }}
                                />
                            </div>
                        </div>

                        <p style={{ fontFamily: sv.mono, fontSize: 12, color: sv.inkDim, margin: 0 }}>
                            Arm a drive with the identity below. The next disc inserted there
                            will adopt it and rip unattended.
                        </p>

                        {/* Drive — editable so multi-drive setups can target a specific
                            drive rather than the caller's best-effort default. */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Drive</SvLabel>
                            <input
                                type="text"
                                value={drive}
                                onChange={(e) => setDrive(e.target.value)}
                                aria-label="Drive to arm"
                                placeholder="E:"
                                style={{
                                    width: 128,
                                    background: sv.bg0,
                                    border: `1px solid ${sv.lineMid}`,
                                    color: sv.cyanHi,
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    padding: '10px 12px',
                                    outline: 'none',
                                }}
                                onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                onBlur={(e) => (e.currentTarget.style.borderColor = sv.lineMid)}
                            />
                        </div>

                        <IdentityFields
                            value={identity}
                            onChange={setIdentity}
                            onSearchQueryChange={(query) => setHasSearchQuery(!!query)}
                            autoFocus
                        />

                        {/* Disc number */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <SvLabel size={10}>Disc Number (optional)</SvLabel>
                            <input
                                type="number"
                                min={1}
                                max={99}
                                value={discNumber}
                                onChange={(e) => setDiscNumber(e.target.value)}
                                placeholder="1"
                                style={{
                                    width: 128,
                                    background: sv.bg0,
                                    border: `1px solid ${sv.lineMid}`,
                                    color: sv.cyanHi,
                                    fontFamily: sv.mono,
                                    fontSize: 13,
                                    padding: '10px 12px',
                                    outline: 'none',
                                }}
                                onFocus={(e) => (e.currentTarget.style.borderColor = sv.cyan)}
                                onBlur={(e) => (e.currentTarget.style.borderColor = sv.lineMid)}
                            />
                        </div>

                        {/* Callout */}
                        <div
                            style={{
                                display: 'flex',
                                gap: 12,
                                alignItems: 'flex-start',
                                padding: 12,
                                border: `1px solid ${sv.magenta}4d`,
                                background: `${sv.magenta}0d`,
                            }}
                        >
                            <IcoDrive
                                size={16}
                                color={sv.magenta}
                                style={{ marginTop: 2, flexShrink: 0, filter: `drop-shadow(0 0 4px ${sv.magenta}99)` }}
                            />
                            <p
                                style={{
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    color: `${sv.magentaHi}cc`,
                                    margin: 0,
                                    lineHeight: 1.5,
                                }}
                            >
                                Episode matching still runs automatically. Anything it cannot resolve
                                lands in the Review Queue as usual.
                            </p>
                        </div>

                        {error && (
                            <div
                                role="alert"
                                style={{
                                    display: 'flex',
                                    gap: 12,
                                    alignItems: 'flex-start',
                                    padding: 12,
                                    border: `1px solid ${sv.red}4d`,
                                    background: `${sv.red}0d`,
                                }}
                            >
                                <IcoError
                                    size={16}
                                    color={sv.red}
                                    style={{ marginTop: 2, flexShrink: 0 }}
                                />
                                <p
                                    style={{
                                        fontFamily: sv.mono,
                                        fontSize: 11,
                                        color: `${sv.red}cc`,
                                        margin: 0,
                                        lineHeight: 1.5,
                                    }}
                                >
                                    {error}
                                </p>
                            </div>
                        )}

                        <div style={{ height: 1, background: sv.line }} />

                        {/* Action Buttons */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            <motion.button
                                type="button"
                                onClick={onClose}
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.97 }}
                                onMouseEnter={(e) => {
                                    e.currentTarget.style.color = sv.ink;
                                    e.currentTarget.style.borderColor = sv.lineHi;
                                }}
                                onMouseLeave={(e) => {
                                    e.currentTarget.style.color = sv.inkDim;
                                    e.currentTarget.style.borderColor = sv.lineMid;
                                }}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: sv.inkDim,
                                    border: `1px solid ${sv.lineMid}`,
                                    background: 'transparent',
                                    boxShadow: 'none',
                                    cursor: 'pointer',
                                }}
                            >
                                Cancel
                            </motion.button>

                            <motion.button
                                type="button"
                                onClick={handleSubmit}
                                disabled={!canSubmit}
                                data-testid="arm-submit"
                                whileHover={canSubmit ? { scale: 1.02 } : {}}
                                whileTap={canSubmit ? { scale: 0.97 } : {}}
                                style={{
                                    flex: 1,
                                    padding: '10px 16px',
                                    fontFamily: sv.mono,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    letterSpacing: '0.18em',
                                    textTransform: 'uppercase',
                                    color: canSubmit ? sv.cyan : `${sv.cyan}4d`,
                                    border: `1px solid ${canSubmit ? sv.cyan : `${sv.cyan}33`}`,
                                    background: canSubmit ? `${sv.cyan}1f` : 'transparent',
                                    boxShadow: canSubmit
                                        ? `0 0 16px ${sv.cyan}4d, inset 0 0 8px ${sv.cyan}0d`
                                        : 'none',
                                    cursor: canSubmit ? 'pointer' : 'not-allowed',
                                    opacity: canSubmit ? 1 : 0.3,
                                }}
                            >
                                {busy ? 'Arming…' : 'Arm Drive'}
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
                                background: sv.magenta,
                                filter: `drop-shadow(0 0 3px ${sv.magenta}cc)`,
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
                            {driveId} · Awaiting Disc
                        </span>
                    </div>
                </SvPanel>
            </motion.div>
        </motion.div>
    );
}

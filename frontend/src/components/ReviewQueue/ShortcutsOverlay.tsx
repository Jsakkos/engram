import { useEffect, useRef } from 'react';
import { SvActionButton, SvPanel, sv } from '../../app/components/synapse';
import { REVIEW_SHORTCUTS } from './keymap';

/** A single key cap. */
function Key({ children }: { children: string }) {
    return (
        <kbd
            style={{
                display: 'inline-block',
                minWidth: 22,
                padding: '2px 7px',
                textAlign: 'center',
                fontFamily: sv.mono,
                fontSize: 11,
                color: sv.cyanHi,
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
            }}
        >
            {children}
        </kbd>
    );
}

/**
 * The `?` overlay: lists every review shortcut. A real modal dialog, so focus
 * moves into it, Escape (or `?` again) closes it, and focus returns to
 * whatever had it before. The list is rendered from the same table the key
 * resolver is documented by, so it cannot go stale.
 */
export function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
    const closeRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        closeRef.current?.querySelector('button')?.focus();
        return () => previous?.focus?.();
    }, []);

    return (
        <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-shortcuts-title"
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            onKeyDown={(e) => {
                if (e.key === 'Escape' || e.key === '?') {
                    e.preventDefault();
                    e.stopPropagation();
                    onClose();
                }
            }}
        >
            <div
                className="absolute inset-0"
                style={{ background: `${sv.bg0}d9`, backdropFilter: 'blur(4px)' }}
                onClick={onClose}
                aria-hidden="true"
            />
            <div className="relative w-full max-w-md">
                <SvPanel glow pad={24} style={{ background: `linear-gradient(180deg, ${sv.bg2}, ${sv.bg1})` }}>
                    <h2
                        id="review-shortcuts-title"
                        style={{
                            margin: '0 0 16px',
                            fontFamily: sv.display,
                            fontSize: 16,
                            letterSpacing: '0.2em',
                            textTransform: 'uppercase',
                            color: sv.cyan,
                        }}
                    >
                        Keyboard shortcuts
                    </h2>
                    <dl style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {REVIEW_SHORTCUTS.map((s) => (
                            <div key={s.description} style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                                <dt style={{ display: 'flex', gap: 5, minWidth: 96 }}>
                                    {s.labels.map((l) => (
                                        <Key key={l}>{l}</Key>
                                    ))}
                                </dt>
                                <dd style={{ margin: 0, fontFamily: sv.mono, fontSize: 12, color: sv.ink }}>
                                    {s.description}
                                </dd>
                            </div>
                        ))}
                    </dl>
                    <p style={{ margin: '16px 0', fontFamily: sv.mono, fontSize: 11, lineHeight: 1.5, color: sv.inkDim }}>
                        Shortcuts pause while you are typing in a field or a dialog is open.
                    </p>
                    <div ref={closeRef} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <SvActionButton tone="neutral" size="sm" onClick={onClose}>
                            Close
                        </SvActionButton>
                    </div>
                </SvPanel>
            </div>
        </div>
    );
}

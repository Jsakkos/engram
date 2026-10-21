import { useEffect, useRef } from 'react';
import { isTypingTarget, resolveReviewKey, type ReviewShortcut } from './keymap';

/**
 * Listen for review-page shortcuts on the window. Ignores keys aimed at a form
 * control or dialog, chords with Ctrl/Cmd/Alt, and events something else has
 * already handled. The handler is read through a ref so callers can pass a
 * fresh closure every render without re-registering the listener.
 */
export function useReviewShortcuts(enabled: boolean, onShortcut: (s: ReviewShortcut) => void): void {
    const handlerRef = useRef(onShortcut);
    handlerRef.current = onShortcut;

    useEffect(() => {
        if (!enabled) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.defaultPrevented || isTypingTarget(e.target)) return;
            const shortcut = resolveReviewKey(e);
            if (!shortcut) return;
            // Arrows and `/` have browser meanings (scroll, quick find); only
            // claim them once we know we are acting on them.
            e.preventDefault();
            handlerRef.current(shortcut);
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [enabled]);
}

/**
 * Keyboard shortcuts for the review page (#716).
 *
 * `resolveReviewKey` is the single source of truth for what a keypress means,
 * and `REVIEW_SHORTCUTS` is what the `?` overlay renders, so the two cannot
 * drift apart. Both are pure so the key map is unit-testable without a DOM.
 */

export type ReviewShortcut =
    | { type: 'move'; delta: 1 | -1 }
    | { type: 'accept'; rank: 1 | 2 | 3 }
    | { type: 'extra' }
    | { type: 'skip' }
    | { type: 'focusPicker' }
    | { type: 'help' };

export interface ReviewKeyInput {
    key: string;
    ctrlKey?: boolean;
    metaKey?: boolean;
    altKey?: boolean;
    shiftKey?: boolean;
    repeat?: boolean;
}

/**
 * Map a keypress to a review action, or null when it is not ours. Any
 * Ctrl/Cmd/Alt chord is left alone so browser and OS shortcuts keep working.
 * Shift is only meaningful for `?` (it is how that character is typed); a
 * shifted letter is not a shortcut, which also keeps Caps Lock from firing them.
 */
export function resolveReviewKey(e: ReviewKeyInput): ReviewShortcut | null {
    if (e.ctrlKey || e.metaKey || e.altKey) return null;
    if (e.key === '?') return { type: 'help' };
    if (e.shiftKey) return null;

    // Navigation repeats while held; the one-shot actions below must not, or
    // holding `e` would toggle a track in and out of extras.
    switch (e.key) {
        case 'j':
        case 'ArrowDown':
            return { type: 'move', delta: 1 };
        case 'k':
        case 'ArrowUp':
            return { type: 'move', delta: -1 };
    }
    if (e.repeat) return null;
    switch (e.key) {
        case '1':
            return { type: 'accept', rank: 1 };
        case '2':
            return { type: 'accept', rank: 2 };
        case '3':
            return { type: 'accept', rank: 3 };
        case 'e':
            return { type: 'extra' };
        case 's':
            return { type: 'skip' };
        case '/':
            return { type: 'focusPicker' };
        default:
            return null;
    }
}

/** Inputs that do not take text, so focus on them must not disable shortcuts. */
const NON_TEXT_INPUT_TYPES = new Set(['checkbox', 'radio', 'button', 'submit', 'reset', 'range']);

/**
 * True when a keypress aimed at `target` belongs to that element rather than to
 * the page: text entry, selects, and anything inside a dialog (which owns its
 * own keys, so a shortcut cannot act on the page behind a modal).
 */
export function isTypingTarget(target: EventTarget | null): boolean {
    if (!(target instanceof Element)) return false;
    if (target.closest('[role="dialog"]')) return true;
    if (target instanceof HTMLInputElement) return !NON_TEXT_INPUT_TYPES.has(target.type);
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
    if (target.getAttribute('contenteditable') === 'true') return true;
    if (target instanceof HTMLElement && target.isContentEditable) return true;
    const role = target.getAttribute('role');
    return role === 'textbox' || role === 'combobox' || role === 'searchbox';
}

export interface ShortcutDoc {
    /** Raw `KeyboardEvent.key` values, used for tests and `aria-keyshortcuts`. */
    keys: string[];
    /** Human labels shown as key caps. */
    labels: string[];
    description: string;
}

export const REVIEW_SHORTCUTS: ShortcutDoc[] = [
    { keys: ['j', 'ArrowDown'], labels: ['J', '↓'], description: 'Next track' },
    { keys: ['k', 'ArrowUp'], labels: ['K', '↑'], description: 'Previous track' },
    { keys: ['1', '2', '3'], labels: ['1', '2', '3'], description: 'Accept ranked candidate 1, 2 or 3' },
    { keys: ['e'], labels: ['E'], description: 'Mark track as extra' },
    { keys: ['s'], labels: ['S'], description: 'Skip track (leave unresolved)' },
    { keys: ['/'], labels: ['/'], description: 'Focus the episode picker' },
    { keys: ['?'], labels: ['?'], description: 'Show or hide this list' },
];

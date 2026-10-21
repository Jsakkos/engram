import { describe, expect, it } from 'vitest';
import { REVIEW_SHORTCUTS, isTypingTarget, resolveReviewKey } from './keymap';

type KeyInput = Parameters<typeof resolveReviewKey>[0];

function key(k: string, extra: Partial<KeyInput> = {}) {
    return resolveReviewKey({ key: k, ...extra });
}

describe('resolveReviewKey', () => {
    it.each([
        ['j', { type: 'move', delta: 1 }],
        ['ArrowDown', { type: 'move', delta: 1 }],
        ['k', { type: 'move', delta: -1 }],
        ['ArrowUp', { type: 'move', delta: -1 }],
        ['1', { type: 'accept', rank: 1 }],
        ['2', { type: 'accept', rank: 2 }],
        ['3', { type: 'accept', rank: 3 }],
        ['e', { type: 'extra' }],
        ['s', { type: 'skip' }],
        ['/', { type: 'focusPicker' }],
        ['?', { type: 'help' }],
    ])('maps %s', (k, expected) => {
        expect(key(k, k === '?' ? { shiftKey: true } : {})).toEqual(expected);
    });

    it('ignores unmapped keys', () => {
        expect(key('x')).toBeNull();
        expect(key('4')).toBeNull();
        expect(key('Enter')).toBeNull();
    });

    it('never fires with a browser/OS modifier held', () => {
        for (const mod of ['ctrlKey', 'metaKey', 'altKey'] as const) {
            expect(key('j', { [mod]: true })).toBeNull();
            expect(key('1', { [mod]: true })).toBeNull();
            expect(key('/', { [mod]: true })).toBeNull();
            expect(key('?', { [mod]: true })).toBeNull();
        }
    });

    it('does not treat shifted letters as shortcuts', () => {
        expect(key('J', { shiftKey: true })).toBeNull();
        expect(key('E', { shiftKey: true })).toBeNull();
    });

    it('ignores key repeat for one-shot actions but keeps navigation repeating', () => {
        expect(key('e', { repeat: true })).toBeNull();
        expect(key('1', { repeat: true })).toBeNull();
        expect(key('j', { repeat: true })).toEqual({ type: 'move', delta: 1 });
    });
});

describe('isTypingTarget', () => {
    const make = (html: string) => {
        const host = document.createElement('div');
        host.innerHTML = html;
        document.body.appendChild(host);
        return host.firstElementChild as HTMLElement;
    };

    it.each([
        '<input type="text" />',
        '<input />',
        '<input type="search" />',
        '<textarea></textarea>',
        '<select><option>a</option></select>',
        '<div contenteditable="true"></div>',
        '<div role="textbox"></div>',
        '<div role="combobox"></div>',
    ])('treats %s as typing', (html) => {
        expect(isTypingTarget(make(html))).toBe(true);
    });

    it('does not treat a checkbox or button as typing, so a clicked row checkbox keeps shortcuts alive', () => {
        expect(isTypingTarget(make('<input type="checkbox" />'))).toBe(false);
        expect(isTypingTarget(make('<button>x</button>'))).toBe(false);
    });

    it('treats anything inside a dialog as typing so modals own their keys', () => {
        const dialog = make('<div role="dialog"><button id="b">x</button></div>');
        expect(isTypingTarget(dialog.querySelector('#b'))).toBe(true);
    });

    it('is false for no target', () => {
        expect(isTypingTarget(null)).toBe(false);
    });
});

describe('REVIEW_SHORTCUTS', () => {
    it('documents every key the resolver handles', () => {
        const documented = REVIEW_SHORTCUTS.flatMap((s) => s.keys);
        for (const k of ['j', 'k', 'ArrowDown', 'ArrowUp', '1', '2', '3', 'e', 's', '/', '?']) {
            expect(documented).toContain(k);
        }
    });
});

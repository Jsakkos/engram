import { useMemo, useState } from 'react';
import {
    applySettingsImport,
    type ImportPreview,
    type ImportPreviewField,
    SettingsImportError,
    settingLabel,
} from './settings/settingsTransfer';
import './SettingsTransfer.css';

interface ImportSettingsDialogProps {
    /** The parsed file, re-sent on apply: the server holds no pending import. */
    file: unknown;
    preview: ImportPreview;
    onCancel: () => void;
    onApplied: (keys: string[]) => void;
    /** The Settings form has edits the post-import reload would replace. */
    hasUnsavedEdits?: boolean;
}

function show(value: unknown): string {
    if (value === null || value === undefined || value === '') return '(empty)';
    if (typeof value === 'boolean') return value ? 'On' : 'Off';
    return String(value);
}

/**
 * Review step of a settings import: every setting in the file, grouped by what
 * applying it would do, with a checkbox per change. Rendered inline in the
 * Settings pane rather than as a second modal over the wizard.
 */
export default function ImportSettingsDialog({
    file,
    preview,
    onCancel,
    onApplied,
    hasUnsavedEdits = false,
}: ImportSettingsDialogProps) {
    const [selected, setSelected] = useState<Set<string>>(
        () => new Set(preview.fields.filter((f) => f.selected).map((f) => f.key)),
    );
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

    const groups = useMemo(() => {
        const changes = preview.fields.filter((f) => f.status === 'change');
        return {
            changes: changes.filter((f) => !f.path_missing && !f.path_unchecked && !f.needs_review),
            review: changes.filter((f) => f.needs_review && !f.path_missing && !f.path_unchecked),
            missing: changes.filter((f) => f.path_missing),
            network: changes.filter((f) => f.path_unchecked),
            invalid: preview.fields.filter((f) => f.status === 'invalid'),
            same: preview.fields.filter((f) => f.status === 'same'),
        };
    }, [preview]);

    const toggle = (key: string) =>
        setSelected((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });

    // Apply in the file's order, so the request reads like the review.
    const keys = preview.fields.map((f) => f.key).filter((k) => selected.has(k));

    const apply = async () => {
        setBusy(true);
        setError(null);
        setRowErrors({});
        try {
            onApplied(await applySettingsImport(file, keys));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
            if (e instanceof SettingsImportError) setRowErrors(e.errors);
        } finally {
            setBusy(false);
        }
    };

    const row = (f: ImportPreviewField, selectable: boolean) => {
        const label = settingLabel(f.key);
        const message = rowErrors[f.key] ?? f.error;
        return (
            <li key={f.key} className="transfer-row">
                <label className="transfer-row-label">
                    <input
                        type="checkbox"
                        checked={selected.has(f.key)}
                        disabled={!selectable || busy}
                        onChange={() => toggle(f.key)}
                        aria-label={label}
                    />
                    <span className="transfer-row-name">{label}</span>
                </label>
                <span className="transfer-row-value">
                    {f.secret ? (
                        'will be replaced'
                    ) : (
                        <>
                            <span className="transfer-row-old">{show(f.current)}</span>
                            {' → '}
                            <span className="transfer-row-new">{show(f.incoming)}</span>
                        </>
                    )}
                </span>
                {message && <span className="transfer-row-error">{message}</span>}
            </li>
        );
    };

    const section = (title: string, rows: ImportPreviewField[], selectable: boolean, hint?: string) =>
        rows.length > 0 && (
            <section className="transfer-group">
                <h4 className="transfer-group-title">
                    {title} ({rows.length})
                </h4>
                {hint && <p className="form-hint">{hint}</p>}
                <ul className="transfer-rows">{rows.map((f) => row(f, selectable))}</ul>
            </section>
        );

    return (
        <div className="transfer-review" aria-label="Review settings import">
            <p className="form-hint">
                Settings file from Engram {preview.engram_version ?? 'unknown'}
                {preview.includes_secrets ? ', including keys and passwords' : ''}. Tick what you want to apply.
            </p>
            {preview.warnings.map((w) => (
                <p key={w} className="transfer-warning">
                    {w}
                </p>
            ))}

            {section('Changes', groups.changes, true)}
            {section(
                'Check before applying',
                groups.review,
                true,
                'These choose a server Engram sends your keys or data to, or a program it runs. Tick one only if you recognise the new value.',
            )}
            {section(
                'Not found on this machine',
                groups.missing,
                true,
                'These paths do not exist here. Applying one creates that folder on this machine, so tick it only if that is where you want it.',
            )}
            {section(
                'Network paths (not checked)',
                groups.network,
                true,
                'Engram does not open network paths from an imported file. Tick one if you recognise it.',
            )}
            {section('Invalid', groups.invalid, false)}

            {groups.same.length > 0 && (
                <details className="transfer-group">
                    <summary>Unchanged ({groups.same.length})</summary>
                    <ul className="transfer-rows">{groups.same.map((f) => row(f, false))}</ul>
                </details>
            )}
            {preview.unknown.length > 0 && (
                <details className="transfer-group">
                    <summary>Not recognized ({preview.unknown.length})</summary>
                    <p className="form-hint">
                        This version of Engram does not import these, so they will be skipped.
                    </p>
                    <ul className="transfer-rows">
                        {preview.unknown.map((k) => (
                            <li key={k} className="transfer-row">
                                <span className="transfer-row-name">{k}</span>
                            </li>
                        ))}
                    </ul>
                </details>
            )}

            {hasUnsavedEdits && (
                <p className="transfer-warning">Your unsaved changes in Settings will be replaced.</p>
            )}
            {error && (
                <p className="transfer-error" role="alert">
                    {error}
                </p>
            )}

            <div className="transfer-actions">
                <button type="button" className="transfer-btn" onClick={onCancel} disabled={busy}>
                    Cancel
                </button>
                <button
                    type="button"
                    className="transfer-btn transfer-btn-primary"
                    onClick={apply}
                    disabled={busy || keys.length === 0}
                >
                    {`Apply ${keys.length} ${keys.length === 1 ? 'setting' : 'settings'}`}
                </button>
            </div>
        </div>
    );
}

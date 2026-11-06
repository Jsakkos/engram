import { useRef, useState } from 'react';
import { toast } from 'sonner';
import ImportSettingsDialog from './ImportSettingsDialog';
import { downloadSettings, type ImportPreview, previewSettingsImport } from './settings/settingsTransfer';
import './SettingsTransfer.css';

interface SettingsTransferSectionProps {
    /** Called after an import is applied, so the form can reload from the server. */
    onImported: () => void;
    /** First-run setup offers import alone: there is nothing worth exporting yet. */
    importOnly?: boolean;
    hasUnsavedEdits?: boolean;
}

/**
 * Settings > Import & Export: download the configuration as a file, or
 * import one from another install (#731). Keys and passwords are left out of
 * an export unless the user ticks the box, and an import is reviewed field by
 * field before anything is written.
 */
export default function SettingsTransferSection({
    onImported,
    importOnly = false,
    hasUnsavedEdits = false,
}: SettingsTransferSectionProps) {
    const [includeSecrets, setIncludeSecrets] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [importError, setImportError] = useState<string | null>(null);
    const [pending, setPending] = useState<{ file: unknown; preview: ImportPreview } | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    const exportSettings = async () => {
        setExporting(true);
        try {
            await downloadSettings(includeSecrets);
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Export failed');
        } finally {
            setExporting(false);
        }
    };

    const chooseFile = async (chosen: File | undefined) => {
        setImportError(null);
        if (inputRef.current) inputRef.current.value = '';
        if (!chosen) return;
        let file: unknown;
        try {
            file = JSON.parse(await chosen.text());
        } catch {
            setImportError('That file is not valid JSON.');
            return;
        }
        try {
            setPending({ file, preview: await previewSettingsImport(file) });
        } catch (e) {
            setImportError(e instanceof Error ? e.message : String(e));
        }
    };

    const applied = (keys: string[]) => {
        setPending(null);
        toast.success(`Imported ${keys.length} ${keys.length === 1 ? 'setting' : 'settings'}`);
        onImported();
    };

    if (pending) {
        return (
            <ImportSettingsDialog
                file={pending.file}
                preview={pending.preview}
                onCancel={() => setPending(null)}
                onApplied={applied}
                hasUnsavedEdits={hasUnsavedEdits}
            />
        );
    }

    return (
        <div className="settings-transfer">
            {!importOnly && (
                <div className="form-group">
                    <label>Export settings</label>
                    <span className="form-hint">
                        Saves your paths, preferences, naming formats and notification settings to a file you can
                        import on another install.
                    </span>
                    <label className="checkbox-label checkbox-plain">
                        <input
                            type="checkbox"
                            checked={includeSecrets}
                            onChange={(e) => setIncludeSecrets(e.target.checked)}
                        />
                        <span className="checkbox-text">Include keys and passwords</span>
                    </label>
                    {includeSecrets && (
                        <p className="transfer-warning">
                            Anyone with this file can use your MakeMKV, TMDB, AI and Discord credentials. Store it like
                            a password.
                        </p>
                    )}
                    <div className="transfer-actions">
                        <button type="button" className="transfer-btn" onClick={exportSettings} disabled={exporting}>
                            Download settings file
                        </button>
                    </div>
                </div>
            )}

            <div className="form-group">
                <label htmlFor="settings-import-file">Import settings</label>
                <span className="form-hint">
                    Choose a file exported from Engram. You will see every change before anything is applied. Setup
                    status, LAN access and data-sharing consent are never imported.
                </span>
                <input
                    ref={inputRef}
                    id="settings-import-file"
                    type="file"
                    accept=".json,application/json"
                    aria-label="Choose a settings file"
                    onChange={(e) => chooseFile(e.target.files?.[0])}
                />
                {importError && (
                    <p className="transfer-error" role="alert">
                        {importError}
                    </p>
                )}
            </div>
        </div>
    );
}

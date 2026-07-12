import { useBackgroundEffectsEnabled } from '../app/hooks/useBackgroundEffectsEnabled';

/**
 * Self-contained toggle for the ambient falling-code rip animation
 * (SvRipAnimation). Reads/writes localStorage directly via the hook rather
 * than routing through ConfigWizard's `config` state — this preference
 * describes the browser rendering the dashboard, not the backend host, so it
 * never touches AppConfig or the generic config save.
 */
export default function BackgroundEffectsSetting() {
    const [enabled, setEnabled] = useBackgroundEffectsEnabled();

    return (
        <div className="form-group checkbox-group">
            <label className="checkbox-label">
                <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                />
                <span className="checkbox-text">
                    <strong>Background Animation</strong>
                    <span className="checkbox-hint">
                        The falling-code effect shown behind the dashboard while a disc is
                        ripping. Turn this off on low-power devices (e.g. ARM64 single-board
                        computers) to reduce CPU/GPU usage. Independent of your OS's
                        reduced-motion setting — takes effect immediately, no restart needed.
                    </span>
                </span>
            </label>
        </div>
    );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { ASR_CYAN as CYAN, type AsrStatus, gpuDownloadGb as gb } from "../app/components/asrStatus";

/**
 * Self-contained GPU-acceleration control for the settings wizard.
 *
 * GPU ASR (faster-whisper/CTranslate2) needs the NVIDIA cuDNN + cuBLAS runtime (~1.2 GB),
 * which is downloaded on demand into ~/.engram/cuda/ rather than bundled. This panel owns its
 * own /api/asr-status fetch and drives the dedicated enable/disable endpoints (so the generic
 * config save can never accidentally kick off the download). Activation takes effect after a
 * backend restart. Only NVIDIA on Windows/Linux is supported; macOS/AMD stay on CPU.
 */

export default function GpuAccelerationSetting() {
    const [status, setStatus] = useState<AsrStatus | null>(null);
    const [busy, setBusy] = useState(false);
    const [eulaAccepted, setEulaAccepted] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const pollRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const fetchStatus = useCallback(async () => {
        try {
            const r = await fetch("/api/asr-status");
            if (!r.ok) return;
            const data: AsrStatus = await r.json();
            setStatus(data);
            if (data.gpu_state === "downloading" || data.gpu_state === "installing") {
                pollRef.current = setTimeout(fetchStatus, 1500);
            }
        } catch {
            /* best-effort */
        }
    }, []);

    useEffect(() => {
        fetchStatus();
        return () => {
            if (pollRef.current) clearTimeout(pollRef.current);
        };
    }, [fetchStatus]);

    const enable = async () => {
        setBusy(true);
        setActionError(null);
        try {
            const r = await fetch("/api/asr/gpu/enable", { method: "POST" });
            if (!r.ok) {
                const body = await r.json().catch(() => ({}));
                throw new Error(body.detail || `Request failed (${r.status})`);
            }
            await fetchStatus();
        } catch (e) {
            setActionError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const disable = async () => {
        setBusy(true);
        setActionError(null);
        try {
            await fetch("/api/asr/gpu/disable", { method: "POST" });
            await fetchStatus();
        } catch (e) {
            setActionError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    if (!status) return null;

    const s = status.gpu_state;

    // Platforms with no CUDA path: don't show a useless toggle, just explain.
    if (s === "unsupported_os") {
        return (
            <div className="form-group">
                <label>GPU Acceleration</label>
                <span className="form-hint">
                    Not available on this platform. GPU transcription requires an NVIDIA GPU on
                    Windows or Linux — macOS and AMD GPUs run on CPU.
                </span>
            </div>
        );
    }
    if (s === "unavailable") {
        return (
            <div className="form-group">
                <label>GPU Acceleration</label>
                <span className="form-hint">
                    No NVIDIA GPU detected. Episode transcription runs on the CPU.
                </span>
            </div>
        );
    }

    const dl = status.gpu_download;
    const pct = dl.total > 0 ? Math.floor((dl.downloaded / dl.total) * 100) : 0;

    return (
        <div className="form-group">
            <label>GPU Acceleration</label>

            {s === "active" && (
                <>
                    <span className="form-hint" style={{ color: CYAN }}>
                        ✓ Active — transcription runs on the GPU (CUDA · {status.compute_type}).
                    </span>
                    <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        onClick={disable}
                        style={{ marginTop: 8, alignSelf: "flex-start" }}
                    >
                        Disable GPU acceleration
                    </button>
                </>
            )}

            {(s === "downloading" || s === "installing") && (
                <>
                    <span className="form-hint">
                        {s === "installing"
                            ? "Installing CUDA runtime…"
                            : `Downloading NVIDIA CUDA runtime… ${pct}% (${gb(dl.downloaded)} / ${gb(
                                  dl.total,
                              )})`}
                    </span>
                    <div
                        style={{
                            marginTop: 6,
                            height: 6,
                            background: "rgba(136,147,168,0.2)",
                            overflow: "hidden",
                        }}
                    >
                        <div
                            style={{
                                width: `${pct}%`,
                                height: "100%",
                                background: CYAN,
                                transition: "width 0.4s",
                            }}
                        />
                    </div>
                    <span className="form-hint" style={{ marginTop: 6 }}>
                        You can keep using Engram. Restart the backend once the download finishes to
                        activate the GPU.
                    </span>
                </>
            )}

            {(s === "available_not_installed" || s === "error") && (
                <>
                    <span className="form-hint">
                        An NVIDIA GPU is available. Enabling downloads the cuDNN + cuBLAS runtime
                        (~{gb(status.gpu_download_size_bytes)}, one time) into{" "}
                        <code>~/.engram/cuda/</code>. It persists across app updates. Activation
                        takes effect after a backend restart.
                    </span>
                    <label
                        style={{
                            display: "flex",
                            alignItems: "flex-start",
                            gap: 8,
                            marginTop: 8,
                            fontSize: 13,
                            fontWeight: 400,
                        }}
                    >
                        <input
                            type="checkbox"
                            checked={eulaAccepted}
                            onChange={(e) => setEulaAccepted(e.target.checked)}
                            style={{ marginTop: 3 }}
                        />
                        <span>
                            I accept the{" "}
                            <a
                                href="https://docs.nvidia.com/cuda/eula/index.html"
                                target="_blank"
                                rel="noreferrer"
                                style={{ color: CYAN }}
                            >
                                NVIDIA CUDA EULA
                            </a>{" "}
                            for the cuDNN and cuBLAS libraries.
                        </span>
                    </label>
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={busy || !eulaAccepted}
                        onClick={enable}
                        style={{ marginTop: 8, alignSelf: "flex-start" }}
                    >
                        {busy ? "Starting…" : `Download & enable (~${gb(status.gpu_download_size_bytes)})`}
                    </button>
                </>
            )}

            {s === "available_not_enabled" && (
                <>
                    <span className="form-hint">
                        An NVIDIA GPU is available and the CUDA runtime is installed. Enable GPU
                        acceleration to transcribe on the GPU (takes effect after a backend restart).
                    </span>
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={enable}
                        style={{ marginTop: 8, alignSelf: "flex-start" }}
                    >
                        {busy ? "Enabling…" : "Enable GPU acceleration"}
                    </button>
                </>
            )}

            {(actionError || (dl.state === "error" && dl.error)) && (
                <span className="form-hint" style={{ color: "#f87171", marginTop: 6 }}>
                    {actionError || `Download failed: ${dl.error}`}
                </span>
            )}
        </div>
    );
}

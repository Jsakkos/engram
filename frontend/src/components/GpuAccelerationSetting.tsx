import { useCallback, useEffect, useRef, useState } from "react";
import { ASR_AMBER as AMBER, ASR_CYAN as CYAN, type AsrStatus, gpuDownloadGb as gb } from "../app/components/asrStatus";

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
    const size = gb(status.gpu_download_size_bytes);
    const libsMissing = s === "enabled_not_active" && status.gpu_fallback_reason === "runtime_missing";

    // The button is the only control: clicking it accepts the EULA and starts the download.
    // A separate EULA checkbox used to sit here among the wizard's saved checkboxes; ticking
    // it looked like enabling CUDA, but it was never saved and started nothing (#694).
    const downloadButton = (
        <>
            <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={enable}
                style={{ marginTop: 8, alignSelf: "flex-start" }}
            >
                {busy ? "Starting…" : `Download & enable (~${size})`}
            </button>
            <span className="form-hint" style={{ marginTop: 6 }}>
                The download starts as soon as you click; it is not part of Save. By clicking, you
                accept the{" "}
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
        </>
    );

    const disableButton = (
        <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={disable}
            style={{ marginTop: 8, alignSelf: "flex-start" }}
        >
            Disable GPU acceleration
        </button>
    );

    return (
        <div className="form-group">
            <label>GPU Acceleration</label>

            {s === "active" && (
                <>
                    <span className="form-hint" style={{ color: CYAN }}>
                        ✓ Active — transcription runs on the GPU (CUDA · {status.compute_type}).
                    </span>
                    {disableButton}
                </>
            )}

            {s === "restart_pending" && (
                <>
                    <span className="form-hint" style={{ color: CYAN }}>
                        ✓ Enabled. Restart the backend to start transcribing on the GPU.
                    </span>
                    {disableButton}
                </>
            )}

            {s === "enabled_not_active" && (
                <>
                    <span className="form-hint" style={{ color: AMBER }} data-testid="gpu-fallback-hint">
                        {libsMissing
                            ? "GPU acceleration is on, but the CUDA runtime is not installed, so transcription is running on the CPU. Download it again to use the GPU."
                            : "GPU acceleration is on and the CUDA runtime is installed, but its libraries failed to load at startup, so transcription is running on the CPU. Check engram.log for details; updating the NVIDIA driver often fixes this."}
                    </span>
                    {libsMissing ? downloadButton : disableButton}
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
                        (~{size}, one time) into <code>~/.engram/cuda/</code>. It persists across
                        app updates. Activation takes effect after a backend restart.
                    </span>
                    {downloadButton}
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

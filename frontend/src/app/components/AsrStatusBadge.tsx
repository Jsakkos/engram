import { useEffect, useState } from "react";

type GpuDownload = {
  state: "idle" | "downloading" | "installing" | "error";
  downloaded: number;
  total: number;
  error: string | null;
};

type AsrStatus = {
  device: string;
  compute_type: string;
  model: string;
  workers: number;
  cpu_threads: number | null;
  max_concurrent_matches: number;
  gpu_detected?: boolean;
  gpu_enabled?: boolean;
  gpu_runtime_installed?: boolean;
  gpu_state?:
    | "active"
    | "available_not_enabled"
    | "available_not_installed"
    | "downloading"
    | "installing"
    | "unsupported_os"
    | "unavailable";
  gpu_download?: GpuDownload;
};

const CYAN = "#22d3ee";
const AMBER = "#f5a623";
const GRAY = "#8893a8";

/**
 * Read-only chip showing the *effective* ASR backend. Crucially it reports the device the
 * model actually loads on — so when an NVIDIA GPU is present but unused (libs not downloaded
 * or acceleration off), it shows an actionable "GPU available →" chip that opens Settings,
 * rather than falsely claiming CUDA.
 */
export function AsrStatusBadge({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const [status, setStatus] = useState<AsrStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = () => {
      fetch("/api/asr-status")
        .then((r) => (r.ok ? r.json() : null))
        .then((data: AsrStatus | null) => {
          if (cancelled || !data) return;
          setStatus(data);
          // While a download is in flight, keep polling so the chip animates to completion.
          if (data.gpu_state === "downloading" || data.gpu_state === "installing") {
            timer = setTimeout(poll, 1500);
          }
        })
        .catch(() => {
          /* badge is best-effort; stay hidden on failure */
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!status) return null;

  const gpuState = status.gpu_state;
  const dl = status.gpu_download;
  const pct = dl && dl.total > 0 ? Math.floor((dl.downloaded / dl.total) * 100) : 0;

  let accent = GRAY;
  let label: string;
  let clickable = false;
  let title = `Whisper "${status.model}" · requested ${status.max_concurrent_matches}${
    status.cpu_threads != null ? ` · ${status.cpu_threads} threads/worker` : ""
  } · restart to change`;

  if (status.device === "cuda") {
    accent = CYAN;
    label = `ASR: CUDA · ${status.compute_type} · ${status.workers}w`;
  } else if (gpuState === "downloading") {
    accent = CYAN;
    label = `ASR: GPU libraries ⬇ ${pct}%`;
    title = "Downloading the NVIDIA CUDA runtime (~1.2 GB). Restart to activate when done.";
  } else if (gpuState === "installing") {
    accent = CYAN;
    label = "ASR: GPU installing…";
  } else if (gpuState === "available_not_enabled" || gpuState === "available_not_installed") {
    accent = AMBER;
    label = "ASR: CPU · GPU available →";
    clickable = true;
    title =
      gpuState === "available_not_installed"
        ? "An NVIDIA GPU is available. Click to enable GPU acceleration in Settings (one-time ~1.2 GB download)."
        : "An NVIDIA GPU is available but acceleration is off. Click to enable it in Settings.";
  } else {
    // cpu / unavailable / unsupported_os
    label = `ASR: CPU · ${status.compute_type} · ${status.workers}w`;
  }

  const baseStyle: React.CSSProperties = {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 11,
    letterSpacing: "0.08em",
    color: accent,
    border: `1px solid ${accent}44`,
    background: `${accent}0a`,
    padding: "2px 8px",
    whiteSpace: "nowrap",
  };

  if (clickable && onOpenSettings) {
    return (
      <button
        type="button"
        title={title}
        onClick={onOpenSettings}
        data-testid="asr-status-badge"
        style={{ ...baseStyle, cursor: "pointer" }}
      >
        {label}
      </button>
    );
  }

  return (
    <span title={title} data-testid="asr-status-badge" style={baseStyle}>
      {label}
    </span>
  );
}

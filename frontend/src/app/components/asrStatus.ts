// Shared shape + colors for the ASR / GPU-acceleration status, served by GET /api/asr-status.
// Single source of truth so the dashboard badge and the settings panel can't drift.

export const ASR_CYAN = "#22d3ee";
export const ASR_AMBER = "#f5a623";
export const ASR_GRAY = "#8893a8";

export type GpuDownload = {
  state: "idle" | "downloading" | "installing" | "error";
  downloaded: number;
  total: number;
  error: string | null;
};

export type GpuState =
  | "active"
  | "available_not_enabled"
  | "available_not_installed"
  | "downloading"
  | "installing"
  | "error"
  | "unsupported_os"
  | "unavailable";

export type AsrStatus = {
  device: string;
  compute_type: string;
  model: string;
  workers: number;
  cpu_threads: number | null;
  max_concurrent_matches: number;
  gpu_detected: boolean;
  gpu_enabled: boolean;
  gpu_runtime_installed: boolean;
  gpu_download_size_bytes: number;
  gpu_download: GpuDownload;
  gpu_state: GpuState;
};

export function gpuDownloadGb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

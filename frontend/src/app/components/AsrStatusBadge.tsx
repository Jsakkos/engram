import { useEffect, useState } from "react";

type AsrStatus = {
  device: string;
  compute_type: string;
  model: string;
  workers: number;
  cpu_threads: number | null;
  max_concurrent_matches: number;
};

/** Read-only chip showing the resolved ASR backend (device · compute · N workers). */
export function AsrStatusBadge() {
  const [status, setStatus] = useState<AsrStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/asr-status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        /* badge is best-effort; stay hidden on failure */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status) return null;

  const accent = status.device === "cuda" ? "#22d3ee" : "#8893a8";
  const label = `ASR: ${status.device.toUpperCase()} · ${status.compute_type} · ${status.workers}w`;

  return (
    <span
      title={`Whisper "${status.model}" · requested ${status.max_concurrent_matches}${
        status.cpu_threads != null ? ` · ${status.cpu_threads} threads/worker` : ""
      } · restart to change`}
      style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        letterSpacing: "0.08em",
        color: accent,
        border: `1px solid ${accent}44`,
        background: `${accent}0a`,
        padding: "2px 8px",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

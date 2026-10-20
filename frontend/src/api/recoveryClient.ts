/**
 * Recovery actions for a failed job (#713): retry its ripped files from
 * staging as a new import job, or open/copy the staging folder.
 */
import { useEffect, useState } from "react";
import { ApiError, apiFetch, apiFetchVoid } from "./client";

export interface JobRecovery {
  job_id: number;
  staging_path: string | null;
  staging_exists: boolean;
  file_count: number;
  can_retry: boolean;
  /** Why a retry is not offered; null when it is. */
  reason: string | null;
}

export function getJobRecovery(jobId: number | string): Promise<JobRecovery> {
  return apiFetch<JobRecovery>(`/api/jobs/${jobId}/recovery`);
}

export function retryFromStaging(
  jobId: number | string,
): Promise<{ job_id: number; retried_from: number }> {
  return apiFetch(`/api/jobs/${jobId}/retry-from-staging`, { method: "POST" });
}

export function openStagingFolder(jobId: number | string): Promise<void> {
  return apiFetchVoid(`/api/jobs/${jobId}/open-staging`, { method: "POST" });
}

/** The backend's `detail` for a refused request, or a generic fallback. */
export function recoveryErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Only available on the machine running Engram. Copy the path instead.";
    try {
      const detail = JSON.parse(error.body)?.detail;
      if (typeof detail === "string" && detail) return detail;
    } catch {
      // non-JSON body: fall through
    }
  }
  return fallback;
}

/** Load a job's recovery status; pass null to skip (e.g. the job did not fail). */
export function useJobRecovery(jobId: number | string | null): JobRecovery | null {
  const [recovery, setRecovery] = useState<JobRecovery | null>(null);
  useEffect(() => {
    setRecovery(null);
    if (jobId === null) return;
    let cancelled = false;
    getJobRecovery(jobId)
      .then((r) => { if (!cancelled) setRecovery(r); })
      .catch(() => { /* no recovery affordance if the status can't be read */ });
    return () => { cancelled = true; };
  }, [jobId]);
  return recovery;
}

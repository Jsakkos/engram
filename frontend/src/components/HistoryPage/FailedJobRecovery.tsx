/**
 * Recovery actions for a failed job in the History detail panel (#713).
 *
 * Retry from staging runs the job's ripped files through the import pipeline as
 * a new job; Open folder / Copy path hand the staging folder to the user. The
 * confirm is inline rather than a portal modal: the detail panel closes on any
 * click outside itself, and a portaled dialog counts as outside.
 */
import { useState } from "react";
import { toast } from "sonner";
import { Copy, FolderOpen } from "lucide-react";
import { IcoRetry } from "../../app/components/icons";
import { SvActionButton, SvLabel, SvPanel, sv } from "../../app/components/synapse";
import {
  openStagingFolder,
  recoveryErrorMessage,
  retryFromStaging,
  useJobRecovery,
} from "../../api/recoveryClient";

export function FailedJobRecovery({ jobId }: { jobId: number }) {
  const recovery = useJobRecovery(jobId);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [retriedAs, setRetriedAs] = useState<number | null>(null);

  if (!recovery) return null;

  const handleRetry = async () => {
    setBusy(true);
    try {
      const { job_id } = await retryFromStaging(jobId);
      setRetriedAs(job_id);
      toast.success(`Retrying from staging as job #${job_id}. Follow it on the dashboard.`);
    } catch (error) {
      toast.error(recoveryErrorMessage(error, "Could not retry this job from staging."));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const handleOpen = async () => {
    try {
      await openStagingFolder(jobId);
    } catch (error) {
      toast.error(recoveryErrorMessage(error, "Could not open the staging folder."));
    }
  };

  const handleCopy = () => {
    if (!recovery.staging_path) return;
    navigator.clipboard.writeText(recovery.staging_path).then(
      () => toast.success("Staging path copied."),
      () => toast.error("Could not copy the path."),
    );
  };

  const fileWord = recovery.file_count === 1 ? "file" : "files";

  return (
    <div data-testid="failed-job-recovery">
      <SvLabel>Recover</SvLabel>
      <div style={{ marginTop: 8 }}>
        <SvPanel pad={12}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, fontFamily: sv.mono, fontSize: 11 }}>
            <div style={{ color: sv.inkDim, lineHeight: 1.6 }}>
              {retriedAs !== null
                ? `Retried as job #${retriedAs}.`
                : recovery.can_retry
                  ? `Staging still holds ${recovery.file_count} ripped ${fileWord}. Retrying runs them through import as a new job, keeping this job's title and season. The disc is not needed.`
                  : recovery.reason}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {recovery.can_retry && retriedAs === null && !confirming && (
                <SvActionButton tone="cyan" size="sm" onClick={() => setConfirming(true)}>
                  <IcoRetry size={11} />
                  Retry from staging
                </SvActionButton>
              )}
              {confirming && (
                <>
                  <SvActionButton tone="cyan" size="sm" onClick={handleRetry} disabled={busy}>
                    Start retry
                  </SvActionButton>
                  <SvActionButton tone="neutral" size="sm" onClick={() => setConfirming(false)} disabled={busy}>
                    Not now
                  </SvActionButton>
                </>
              )}
              {recovery.staging_exists && (
                <SvActionButton tone="neutral" size="sm" onClick={handleOpen} title="Open in the file manager on the machine running Engram">
                  <FolderOpen size={11} />
                  Open folder
                </SvActionButton>
              )}
              {recovery.staging_path && (
                <SvActionButton tone="neutral" size="sm" onClick={handleCopy}>
                  <Copy size={11} />
                  Copy path
                </SvActionButton>
              )}
            </div>
          </div>
        </SvPanel>
      </div>
    </div>
  );
}

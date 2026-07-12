import { useEffect, useRef, useState } from 'react';
import { SvActionButton, SvPanel, sv } from '../../app/components/synapse';
import {
  previewManualSubtitles,
  commitManualSubtitles,
  type ManualSubtitlePreviewStatus,
} from '../../api/client';

interface Row {
  filename: string;
  content: string;
  season: number | null;
  episode: number | null;
  status: ManualSubtitlePreviewStatus;
  warning?: string | null;
  checked: boolean;
}

const STATUS_LABEL: Record<ManualSubtitlePreviewStatus, string> = {
  ready: 'Ready to import',
  already_covered: 'Already has a reference (skipped)',
  unparseable: 'Could not detect episode — enter season/episode',
  invalid_content: 'Not a valid subtitle file',
  duplicate: 'Duplicate of another file in this batch',
};

function rowLabel(r: Row): string {
  return r.season != null && r.episode != null
    ? `S${String(r.season).padStart(2, '0')}E${String(r.episode).padStart(2, '0')}`
    : r.filename;
}

async function readFilesAsText(fileList: FileList): Promise<{ filename: string; content: string }[]> {
  return Promise.all(
    Array.from(fileList)
      .filter((f) => f.name.toLowerCase().endsWith('.srt'))
      .map(
        (f) =>
          new Promise<{ filename: string; content: string }>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve({ filename: f.name, content: String(reader.result ?? '') });
            reader.onerror = () => reject(reader.error);
            reader.readAsText(f);
          }),
      ),
  );
}

export function SubtitleUploadModal({
  jobId,
  onImported,
}: {
  jobId: number;
  onImported: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // webkitdirectory is non-standard and not part of React's input prop types,
  // so it's set imperatively rather than as a JSX attribute. The input only
  // exists in the DOM while the modal is open, so this re-runs on every open.
  useEffect(() => {
    inputRef.current?.setAttribute('webkitdirectory', '');
  }, [open]);

  const openPicker = () => {
    setOpen(true);
    setRows([]);
    setError(null);
  };

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const read = await readFilesAsText(fileList);
      const contentByName = new Map(read.map((f) => [f.filename, f.content]));
      const results = await previewManualSubtitles(jobId, read);
      setRows(
        results.map((r) => ({
          filename: r.filename,
          content: contentByName.get(r.filename) ?? '',
          season: r.season,
          episode: r.episode,
          status: r.status,
          warning: r.warning,
          checked: r.status === 'ready',
        })),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to preview files');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const setRowOverride = (filename: string, field: 'season' | 'episode', value: string) => {
    const num = value === '' ? null : Number(value);
    setRows((prev) =>
      prev.map((r) => {
        if (r.filename !== filename) return r;
        const next = { ...r, [field]: num };
        next.checked = next.season != null && next.episode != null && r.status !== 'invalid_content';
        return next;
      }),
    );
  };

  const toggleRow = (filename: string) => {
    setRows((prev) => prev.map((r) => (r.filename === filename ? { ...r, checked: !r.checked } : r)));
  };

  const handleImport = async () => {
    const toImport = rows.filter((r) => r.checked && r.season != null && r.episode != null);
    if (toImport.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await commitManualSubtitles(
        jobId,
        toImport.map((r) => ({
          filename: r.filename,
          season: r.season as number,
          episode: r.episode as number,
          content: r.content,
        })),
      );
      setOpen(false);
      setRows([]);
      onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import subtitles');
    } finally {
      setBusy(false);
    }
  };

  const importCount = rows.filter((r) => r.checked).length;

  return (
    <>
      <SvActionButton tone="cyan" size="sm" onClick={openPicker}>
        Upload Subtitles
      </SvActionButton>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: `${sv.bg0}d9`,
          }}
        >
          <SvPanel glow pad={20} style={{ width: '100%', maxWidth: 640, maxHeight: '80vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div
                style={{
                  fontFamily: sv.mono,
                  fontSize: 13,
                  fontWeight: 700,
                  color: sv.cyanHi,
                  letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                }}
              >
                Upload Subtitles
              </div>

              <input
                ref={inputRef}
                data-testid="subtitle-upload-input"
                type="file"
                multiple
                accept=".srt"
                onChange={handleFiles}
                style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}
              />

              {error && <div style={{ color: sv.red, fontFamily: sv.mono, fontSize: 11 }}>{error}</div>}

              {rows.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {rows.map((r) => (
                    <div
                      key={r.filename}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '6px 8px',
                        border: `1px solid ${sv.lineMid}`,
                        fontFamily: sv.mono,
                        fontSize: 11,
                      }}
                    >
                      <input
                        type="checkbox"
                        aria-label={rowLabel(r)}
                        checked={r.checked}
                        disabled={r.status === 'invalid_content'}
                        onChange={() => toggleRow(r.filename)}
                      />
                      <span style={{ minWidth: 64, color: sv.cyanHi, fontWeight: 700 }}>{rowLabel(r)}</span>
                      <span
                        style={{
                          flex: 1,
                          color: sv.inkDim,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {r.filename}
                      </span>
                      {r.status === 'unparseable' ? (
                        <>
                          <input
                            type="number"
                            placeholder="S"
                            style={{ width: 44 }}
                            onChange={(e) => setRowOverride(r.filename, 'season', e.target.value)}
                          />
                          <input
                            type="number"
                            placeholder="E"
                            style={{ width: 44 }}
                            onChange={(e) => setRowOverride(r.filename, 'episode', e.target.value)}
                          />
                        </>
                      ) : (
                        <span style={{ color: r.status === 'ready' ? sv.green : sv.inkFaint }}>
                          {STATUS_LABEL[r.status]}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                <SvActionButton tone="neutral" size="md" onClick={() => setOpen(false)} disabled={busy}>
                  Cancel
                </SvActionButton>
                <SvActionButton
                  tone="cyan"
                  size="md"
                  onClick={handleImport}
                  disabled={busy || importCount === 0}
                >
                  {`Import (${importCount})`}
                </SvActionButton>
              </div>
            </div>
          </SvPanel>
        </div>
      )}
    </>
  );
}

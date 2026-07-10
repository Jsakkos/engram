import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { motion } from "motion/react";
import { IcoLibrary, IcoFilter, IcoError } from "../app/components/icons";
import { SvPanel, sv } from "../app/components/synapse";
import { formatBytesScaled as fmtBytes } from "../utils/formatting";
import {
  browseDir,
  previewImport,
  startImport,
  type BrowseEntry,
  type PreviewResult,
} from "../api/client";

interface Props {
  onClose: () => void;
  defaultPath: string;
  defaultDestinationMode: "library" | "in_place";
}

export default function ImportModal({ onClose, defaultPath, defaultDestinationMode }: Props) {
  const [cwd, setCwd] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<BrowseEntry[]>([]);
  const [roots, setRoots] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [destMode, setDestMode] = useState<"library" | "in_place">(defaultDestinationMode);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [pathInput, setPathInput] = useState(defaultPath || "");
  const dialogRef = useRef<HTMLDivElement>(null);
  // Monotonic request tokens so a slow earlier click can't overwrite the state
  // of a later one (navigate and choose fire together per directory click).
  const navSeq = useRef(0);
  const chooseSeq = useRef(0);
  // Set once the user edits the path field, cleared when they submit it. Guards
  // the cwd sync below; a ref, not state, since it must not trigger a re-render
  // and must be readable in the same commit that applies a new cwd.
  const pathDirty = useRef(false);

  // Returns false when the browse failed OR was superseded by a newer navigation.
  // Only the failure case surfaces an error notice; callers must not chain a
  // preview off a false return either way.
  const navigate = useCallback(async (path: string): Promise<boolean> => {
    const seq = ++navSeq.current;
    setError(null);
    try {
      const res = await browseDir(path);
      if (seq !== navSeq.current) return false; // a newer navigation superseded this one
      setCwd(res.cwd);
      setParent(res.parent);
      setEntries(res.entries);
      setRoots(res.roots);
      return true;
    } catch (e) {
      if (seq === navSeq.current) {
        setError(e instanceof Error ? e.message : "Could not read directory");
      }
      return false;
    }
  }, []);

  useEffect(() => {
    navigate(defaultPath || "");
  }, [navigate, defaultPath]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  // Keep the field showing the current directory, but never overwrite text the
  // user is actively typing: a slow browse can resolve mid-keystroke.
  useEffect(() => {
    if (cwd && !pathDirty.current) setPathInput(cwd);
  }, [cwd]);

  const choose = useCallback(async (path: string) => {
    const seq = ++chooseSeq.current;
    setSelected(path);
    setPreview(null);
    setError(null);
    try {
      const result = await previewImport(path);
      if (seq !== chooseSeq.current) return; // a newer selection superseded this one
      setPreview(result);
    } catch (e) {
      if (seq === chooseSeq.current) {
        setError(e instanceof Error ? e.message : "Could not scan folder");
      }
    }
  }, []);

  const submitPath = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const target = pathInput.trim();
      if (!target) return;
      // Hand the field back to the cwd sync. On a failed browse cwd never
      // changes, so no sync fires and the bad text stays for the user to fix.
      pathDirty.current = false;
      // Mirrors the directory-click gesture: browse into it, and preview it.
      // Only preview if the browse resolved, so one typo yields one error.
      if (await navigate(target)) await choose(target);
    },
    [pathInput, navigate, choose],
  );

  const onStart = useCallback(async () => {
    if (!selected || !preview || preview.total_jobs === 0) return;
    setStarting(true);
    setError(null);
    try {
      await startImport(selected, destMode);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed to start");
      setStarting(false);
    }
  }, [selected, preview, destMode, onClose]);

  const seasonsByShow = (p: PreviewResult) => {
    const map = new Map<string, typeof p.units>();
    for (const u of p.units) {
      const key = u.show_name ?? "Unknown";
      map.set(key, [...(map.get(key) ?? []), u]);
    }
    return [...map.entries()];
  };

  return (
    <motion.div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ outline: "none" }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Import media"
    >
      <motion.div
        className="absolute inset-0"
        style={{ background: `${sv.bg0}d9`, backdropFilter: "blur(4px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
        data-testid="import-backdrop"
      />
      <motion.div
        className="relative w-full"
        style={{ maxWidth: 820, maxHeight: "82vh", minHeight: 340, display: "flex" }}
        initial={{ opacity: 0, scale: 0.96, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 16 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
      >
        <SvPanel
          glow
          pad={0}
          testid="import-panel"
          style={{
            background: sv.bg1,
            display: "flex",
            flexDirection: "column",
            flex: 1,
            minHeight: 0,
          }}
        >
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "14px 18px",
              borderBottom: `1px solid ${sv.line}`,
              flexShrink: 0,
            }}
          >
            <IcoLibrary size={18} color={sv.cyan} />
            <span
              style={{
                fontFamily: sv.mono,
                fontWeight: 700,
                letterSpacing: "0.2em",
                fontSize: 13,
                color: sv.cyanHi,
              }}
            >
              IMPORT MEDIA
            </span>
            <button
              onClick={onClose}
              aria-label="Close"
              data-testid="import-close-btn"
              style={{
                marginLeft: "auto",
                background: "transparent",
                border: "none",
                color: sv.inkDim,
                cursor: "pointer",
                fontSize: 16,
              }}
            >
              ✕
            </button>
          </div>

          <form
            onSubmit={submitPath}
            data-testid="import-path-form"
            style={{
              display: "flex",
              gap: 6,
              padding: "8px 12px",
              borderBottom: `1px solid ${sv.line}`,
              flexShrink: 0,
            }}
          >
            <input
              data-testid="import-path-input"
              value={pathInput}
              onChange={(e) => {
                pathDirty.current = true;
                setPathInput(e.target.value);
              }}
              spellCheck={false}
              aria-label="Path"
              placeholder="Type or paste a folder path"
              style={{
                flex: 1,
                minWidth: 0,
                fontFamily: sv.mono,
                fontSize: 11,
                padding: "5px 8px",
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
                color: sv.ink,
                outline: "none",
              }}
            />
            <button
              type="submit"
              data-testid="import-path-go"
              style={{
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.1em",
                padding: "5px 12px",
                border: `1px solid ${sv.cyan}`,
                background: "transparent",
                color: sv.cyan,
                cursor: "pointer",
              }}
            >
              GO
            </button>
          </form>

          <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
            {/* Left: navigator */}
            <div
              style={{
                width: "46%",
                borderRight: `1px solid ${sv.line}`,
                display: "flex",
                flexDirection: "column",
                minHeight: 0,
              }}
            >
              <div data-testid="import-nav-list" style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
                {parent !== null && (
                  <Row label=".." onClick={() => navigate(parent)} kind="dir" />
                )}
                {roots.map((r) => (
                  <Row key={r} label={r} onClick={() => navigate(r)} kind="dir" />
                ))}
                {entries.map((e) => (
                  <Row
                    key={e.path}
                    label={e.name}
                    count={e.type === "dir" ? e.mkv_count : undefined}
                    kind={e.type}
                    active={selected === e.path}
                    onClick={() =>
                      e.type === "dir"
                        ? (navigate(e.path), choose(e.path))
                        : choose(e.path)
                    }
                  />
                ))}
              </div>
            </div>

            {/* Right: preview */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
              <div
                style={{
                  padding: "8px 14px",
                  fontFamily: sv.mono,
                  fontSize: 9,
                  letterSpacing: "0.2em",
                  color: sv.inkFaint,
                  borderBottom: `1px solid ${sv.line}`,
                }}
              >
                PREVIEW
              </div>
              <div style={{ flex: 1, overflow: "auto", padding: 14, minHeight: 0 }}>
                {!preview && (
                  <p style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkFaint }}>
                    Select a folder or file to preview.
                  </p>
                )}
                {preview && preview.total_jobs === 0 && (
                  <p style={{ fontFamily: sv.mono, fontSize: 11, color: sv.inkDim }}>
                    No MKV files found here.
                  </p>
                )}
                {preview &&
                  seasonsByShow(preview).map(([show, units]) => (
                    <div key={show} style={{ marginBottom: 12 }}>
                      <div
                        style={{
                          fontFamily: sv.mono,
                          fontSize: 13,
                          color: sv.cyanHi,
                          marginBottom: 4,
                        }}
                      >
                        {show}
                      </div>
                      {units.map((u, i) => (
                        <div
                          key={i}
                          style={{
                            display: "flex",
                            gap: 8,
                            fontFamily: sv.mono,
                            fontSize: 11,
                            color: sv.inkDim,
                            padding: "3px 0",
                          }}
                        >
                          <span style={{ width: 90 }}>
                            {u.season != null ? `SEASON ${u.season}` : "ALL SEASONS"}
                          </span>
                          <span style={{ flex: 1 }}>{u.file_count} files</span>
                          <span style={{ color: sv.cyan }}>1 job</span>
                        </div>
                      ))}
                    </div>
                  ))}

                {preview && preview.loose_files.length > 0 && (
                  <Notice
                    text={`${preview.loose_files.length} loose file(s) have no Season folder; they will match across all seasons.`}
                  />
                )}
                {preview?.truncated && (
                  <Notice text="This folder is very large; only part of it was scanned." />
                )}
                {error && <Notice text={error} tone="error" />}
              </div>

              {/* Destination */}
              <div
                style={{ padding: "10px 14px", borderTop: `1px solid ${sv.line}`, flexShrink: 0 }}
              >
                <div
                  style={{
                    fontFamily: sv.mono,
                    fontSize: 9,
                    letterSpacing: "0.15em",
                    color: sv.inkFaint,
                    marginBottom: 6,
                  }}
                >
                  DESTINATION
                </div>
                <div style={{ display: "flex" }}>
                  {(["library", "in_place"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setDestMode(m)}
                      style={{
                        fontFamily: sv.mono,
                        fontSize: 10,
                        padding: "5px 11px",
                        cursor: "pointer",
                        border: `1px solid ${sv.lineMid}`,
                        background: destMode === m ? sv.cyan : "transparent",
                        color: destMode === m ? sv.bg0 : sv.inkDim,
                        fontWeight: destMode === m ? 700 : 400,
                        marginRight: m === "library" ? -1 : 0,
                      }}
                    >
                      {m === "library" ? "Organize into library" : "Organize in place"}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "12px 16px",
              borderTop: `1px solid ${sv.line}`,
              flexShrink: 0,
            }}
          >
            <span style={{ fontFamily: sv.mono, fontSize: 10, color: sv.inkFaint }}>
              {preview
                ? `${preview.total_jobs} jobs · ${preview.total_files} files · ${fmtBytes(preview.total_bytes)}`
                : ""}
            </span>
            <button
              onClick={onClose}
              style={{
                marginLeft: "auto",
                fontFamily: sv.mono,
                fontSize: 10,
                padding: "7px 14px",
                border: `1px solid ${sv.lineMid}`,
                background: "transparent",
                color: sv.inkDim,
                cursor: "pointer",
              }}
            >
              CANCEL
            </button>
            <button
              onClick={onStart}
              disabled={!preview || preview.total_jobs === 0 || starting}
              data-testid="import-start-btn"
              style={{
                fontFamily: sv.mono,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.1em",
                padding: "7px 16px",
                border: `1px solid ${sv.cyan}`,
                background:
                  !preview || preview.total_jobs === 0 || starting ? "transparent" : sv.cyan,
                color: !preview || preview.total_jobs === 0 || starting ? sv.inkFaint : sv.bg0,
                cursor:
                  !preview || preview.total_jobs === 0 || starting ? "not-allowed" : "pointer",
              }}
            >
              {starting
                ? "STARTING…"
                : `START IMPORT${preview && preview.total_jobs ? ` · ${preview.total_jobs} JOBS` : ""}`}
            </button>
          </div>
        </SvPanel>
      </motion.div>
    </motion.div>
  );
}

function Row({
  label,
  count,
  kind,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  kind: "dir" | "mkv";
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        textAlign: "left",
        padding: "8px 12px",
        fontFamily: sv.mono,
        fontSize: 11,
        color: active ? sv.cyanHi : sv.inkDim,
        background: active ? `${sv.cyan}14` : "transparent",
        border: "none",
        borderBottom: `1px solid ${sv.line}`,
        boxShadow: active ? `inset 2px 0 0 ${sv.cyan}` : "none",
        cursor: "pointer",
      }}
    >
      <IcoFilter size={12} color={kind === "mkv" ? sv.inkFaint : sv.cyan} />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      {count != null && count > 0 && (
        <span style={{ fontSize: 9, color: sv.cyan }}>{count} mkv</span>
      )}
    </button>
  );
}

function Notice({ text, tone = "warn" }: { text: string; tone?: "warn" | "error" }) {
  const color = tone === "error" ? sv.red : sv.yellow;
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        alignItems: "flex-start",
        marginTop: 10,
        padding: "8px 10px",
        border: `1px solid ${color}4d`,
        background: `${color}14`,
      }}
    >
      <IcoError size={14} color={color} style={{ flexShrink: 0, marginTop: 1 }} />
      <span style={{ fontFamily: sv.mono, fontSize: 10, color, lineHeight: 1.5 }}>{text}</span>
    </div>
  );
}

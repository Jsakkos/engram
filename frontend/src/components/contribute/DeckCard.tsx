import { Send, Wand2, Maximize2, ExternalLink } from "lucide-react";
import { motion } from "motion/react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { sv, SvActionButton } from "../../app/components/synapse";
import { usePosterImage } from "../../app/components/DiscCard/hooks/usePosterImage";
import type { Deck } from "./types";

interface DeckCardProps {
  deck: Deck;
  apiKeySet: boolean;
  onSubmit: (releaseGroupId: string) => void;
  onEnhance: (releaseGroupId: string) => void;
  onFanOut: (releaseGroupId: string) => void;
  submitting: boolean;
  enhanceOpen: boolean;
}

/** Drag/drop payloads shared across DeckCard and FannedDeck. */
export interface DeckDragData {
  kind: "solo-deck";
  jobId: number;
  releaseGroupId: string;
}
export interface DeckDropData {
  kind: "deck-target";
  releaseGroupId: string;
  isSolo: boolean;
  jobIds: number[];
}

const TONE = {
  tv: { border: sv.cyan, glow: "rgba(94, 234, 212, 0.35)", titleColor: sv.cyanHi, label: "TV" },
  movie: { border: sv.magenta, glow: "rgba(255, 61, 127, 0.35)", titleColor: sv.magentaHi, label: "MOVIE" },
  unknown: { border: sv.lineHi, glow: "rgba(94, 234, 212, 0.2)", titleColor: sv.ink, label: "DISC" },
} as const;

function formatRuntime(seconds: number): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/**
 * Stacked-deck visual for a release group on the Contribute page.
 *
 * Renders three offset back-cards behind a top card; the top card carries
 * the poster art (dimmed) and stat block. Solo decks (single disc) skip
 * the back-card stack so the visual matches "one disc, no group."
 */
export function DeckCard({
  deck,
  apiKeySet,
  onSubmit,
  onEnhance,
  onFanOut,
  submitting,
  enhanceOpen,
}: DeckCardProps) {
  const tone = TONE[deck.content_type] ?? TONE.unknown;
  const isMulti = deck.discs.length > 1;
  const showStack = isMulti;
  const anchorJobId = deck.discs[0]?.job_id ?? 0;
  const posterUrl = usePosterImage(String(anchorJobId), deck.title ?? "");

  // Solo decks are draggable so they can be merged into another deck.
  // Multi-disc decks are not draggable themselves; users fan out and drag
  // individual discs (handled by FannedDeck).
  const dragData: DeckDragData = {
    kind: "solo-deck",
    jobId: anchorJobId,
    releaseGroupId: deck.release_group_id,
  };
  const { attributes, listeners, setNodeRef: setDragRef, transform, isDragging } =
    useDraggable({
      id: `deck-drag-${deck.release_group_id}`,
      data: dragData,
      disabled: !deck.is_solo,
    });

  // All decks are drop targets so a solo can be merged in.
  const dropData: DeckDropData = {
    kind: "deck-target",
    releaseGroupId: deck.release_group_id,
    isSolo: deck.is_solo,
    jobIds: deck.discs.map((d) => d.job_id),
  };
  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `deck-drop-${deck.release_group_id}`,
    data: dropData,
  });

  const setRefs = (el: HTMLDivElement | null) => {
    setDragRef(el);
    setDropRef(el);
  };

  const dragStyle: React.CSSProperties = transform
    ? {
        transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        zIndex: 50,
      }
    : {};

  const submitDisabled =
    !apiKeySet ||
    submitting ||
    deck.submission_status.exported === 0 ||
    deck.submission_status.exported + deck.submission_status.submitted === 0;

  // Aggregate top-line status: prefer "submitted" if any complete, else exported, else pending.
  const submitted = deck.submission_status.submitted;
  const exported = deck.submission_status.exported;
  const pending = deck.submission_status.pending;
  const total = deck.discs.length;

  const submittedDisc = deck.discs.find((d) => d.contribute_url);

  return (
    <div
      ref={setRefs}
      style={{
        position: "relative",
        width: 420,
        minHeight: showStack ? 240 : 220,
        cursor: deck.is_solo ? (isDragging ? "grabbing" : "grab") : "default",
        outline: isOver ? `2px dashed ${tone.border}` : "none",
        outlineOffset: 6,
        opacity: isDragging ? 0.5 : 1,
        ...dragStyle,
      }}
      data-testid="deck-card"
      data-release-group-id={deck.release_group_id}
      {...(deck.is_solo ? { ...listeners, ...attributes } : {})}
    >
      {showStack && (
        <>
          <div
            style={{
              position: "absolute",
              top: 18,
              left: 18,
              width: 380,
              height: 200,
              border: `1px solid ${tone.border}33`,
              background: sv.bg1,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 12,
              left: 12,
              width: 380,
              height: 200,
              border: `1px solid ${tone.border}55`,
              background: sv.bg1,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 6,
              left: 6,
              width: 380,
              height: 200,
              border: `1px solid ${tone.border}88`,
              background: sv.bg1,
            }}
          />
        </>
      )}

      <motion.div
        whileHover={{ y: -2 }}
        transition={{ type: "spring", stiffness: 320, damping: 22 }}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 380,
          height: 200,
          padding: 14,
          border: `1.5px solid ${tone.border}`,
          background: posterUrl
            ? `linear-gradient(180deg, rgba(5,7,12,0.35) 0%, rgba(5,7,12,0.92) 100%), url(${posterUrl}) center/cover`
            : `linear-gradient(135deg, ${sv.bg2} 0%, ${sv.bg0} 100%)`,
          boxShadow: `0 0 28px ${tone.glow}, inset 0 0 32px rgba(94,234,212,0.04)`,
          fontFamily: sv.mono,
          overflow: "hidden",
        }}
      >
        {/* L-bracket corner ticks (matches SvPanel motif) */}
        <Tick pos="tl" color={tone.border} />
        <Tick pos="tr" color={tone.border} />
        <Tick pos="bl" color={tone.border} />
        <Tick pos="br" color={tone.border} />

        {/* Header row: title + content-type pill + deck-count pill */}
        <div style={{ position: "relative", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontFamily: sv.display,
                fontSize: 15,
                fontWeight: 600,
                color: tone.titleColor,
                textShadow: "0 1px 6px rgba(0,0,0,0.85)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {deck.title ?? "Unknown"}
            </div>
            <div
              style={{
                color: sv.inkDim,
                fontSize: 11,
                marginTop: 2,
                textShadow: "0 1px 4px rgba(0,0,0,0.85)",
              }}
            >
              {deck.season != null ? `Season ${deck.season} · ` : ""}
              {deck.year ? `${deck.year} · ` : ""}
              {deck.discs.length} {deck.discs.length === 1 ? "disc" : "discs"}
            </div>
          </div>
          <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
            <Pill color={tone.border}>{tone.label}</Pill>
            {isMulti && <Pill color={sv.amber}>Deck {deck.discs.length}</Pill>}
          </div>
        </div>

        {/* Stat grid */}
        <div
          style={{
            position: "relative",
            marginTop: 14,
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            columnGap: 14,
            rowGap: 4,
            fontSize: 11,
          }}
        >
          <Stat k="UPC">{deck.upc_code ?? "—"}</Stat>
          <Stat k="TMDB">{deck.tmdb_id ?? "—"}</Stat>
          {deck.content_type === "tv" ? (
            <Stat k="Episodes">{deck.matched_episodes}</Stat>
          ) : (
            <Stat k="Title">{deck.title ?? "—"}</Stat>
          )}
          <Stat k="Runtime">{formatRuntime(deck.total_runtime_seconds)}</Stat>
          <Stat k="Status">
            <span style={{ color: sv.green }}>
              {submitted > 0 && `${submitted} SUBMITTED`}
              {submitted > 0 && exported > 0 && " · "}
              {exported > 0 && `${exported} EXPORTED`}
              {submitted === 0 && exported === 0 && pending === total && `${pending} PENDING`}
            </span>
          </Stat>
        </div>

        {/* Action row, pinned bottom; stat grid above has bottom padding via the height budget */}
        <div
          style={{
            position: "absolute",
            bottom: 10,
            left: 14,
            right: 14,
            display: "flex",
            gap: 6,
          }}
        >
          {submittedDisc?.contribute_url ? (
            <SvActionButton
              tone="cyan"
              size="sm"
              href={submittedDisc.contribute_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink size={11} /> TheDiscDB
            </SvActionButton>
          ) : (
            <SvActionButton
              tone="cyan"
              size="sm"
              onClick={() => onSubmit(deck.release_group_id)}
              disabled={submitDisabled}
            >
              <Send size={11} /> {isMulti ? "Submit Group" : "Submit"}
            </SvActionButton>
          )}
          <SvActionButton
            tone={enhanceOpen ? "neutral" : "magenta"}
            size="sm"
            onClick={() => onEnhance(deck.release_group_id)}
          >
            <Wand2 size={11} /> Enhance
          </SvActionButton>
          {isMulti && (
            <SvActionButton
              tone="neutral"
              size="sm"
              onClick={() => onFanOut(deck.release_group_id)}
            >
              <Maximize2 size={11} /> Fan Out
            </SvActionButton>
          )}
        </div>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span
      style={{
        color,
        fontFamily: sv.mono,
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: "0.16em",
        textTransform: "uppercase",
        padding: "2px 7px",
        border: `1px solid ${color}`,
        background: "rgba(5,7,12,0.7)",
      }}
    >
      {children}
    </span>
  );
}

function Stat({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <>
      <div
        style={{
          color: sv.inkFaint,
          fontSize: 10,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        {k}
      </div>
      <div
        style={{
          color: sv.ink,
          textAlign: "right",
          textShadow: "0 1px 3px rgba(0,0,0,0.85)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {children}
      </div>
    </>
  );
}

function Tick({ pos, color }: { pos: "tl" | "tr" | "bl" | "br"; color: string }) {
  const base: React.CSSProperties = { position: "absolute", width: 8, height: 8, pointerEvents: "none" };
  if (pos === "tl") return <span style={{ ...base, top: -1, left: -1, borderTop: `1.5px solid ${color}`, borderLeft: `1.5px solid ${color}` }} />;
  if (pos === "tr") return <span style={{ ...base, top: -1, right: -1, borderTop: `1.5px solid ${color}`, borderRight: `1.5px solid ${color}` }} />;
  if (pos === "bl") return <span style={{ ...base, bottom: -1, left: -1, borderBottom: `1.5px solid ${color}`, borderLeft: `1.5px solid ${color}` }} />;
  return <span style={{ ...base, bottom: -1, right: -1, borderBottom: `1.5px solid ${color}`, borderRight: `1.5px solid ${color}` }} />;
}

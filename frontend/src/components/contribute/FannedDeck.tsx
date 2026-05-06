import { motion } from "motion/react";
import { X } from "lucide-react";
import { useDraggable } from "@dnd-kit/core";
import { sv, SvActionButton } from "../../app/components/synapse";
import type { Deck, DeckDiscEntry } from "./types";

export interface FannedDiscDragData {
  kind: "fanned-disc";
  jobId: number;
  sourceReleaseGroupId: string;
}

interface FannedDeckProps {
  deck: Deck;
  onClose: () => void;
  onUngroup: (jobId: number) => void;
}

function formatRuntime(seconds: number): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

const ROTATIONS = [-3, -1, 1, 3, -2, 2];

/**
 * Splayed-out detail view for a multi-disc deck.
 *
 * Each disc card is slightly rotated and individually draggable. Dragging
 * a card outside any deck-target droppable zone ungroups it.
 */
export function FannedDeck({ deck, onClose, onUngroup }: FannedDeckProps) {
  const tone = deck.content_type === "movie" ? sv.magenta : sv.cyan;

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      style={{ overflow: "hidden", marginTop: 16 }}
    >
      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          padding: 16,
          background: `linear-gradient(180deg, ${sv.bg1} 0%, ${sv.bg0} 100%)`,
          border: `1px solid ${tone}33`,
        }}
      >
        {deck.discs.map((disc, idx) => (
          <FannedDiscCard
            key={disc.job_id}
            disc={disc}
            idx={idx}
            tone={tone}
            sourceReleaseGroupId={deck.release_group_id}
            onUngroup={onUngroup}
          />
        ))}

        <div style={{ display: "flex", alignItems: "flex-start", marginLeft: "auto" }}>
          <SvActionButton tone="neutral" size="sm" onClick={onClose}>
            Close
          </SvActionButton>
        </div>
      </div>
    </motion.div>
  );
}

interface FannedDiscCardProps {
  disc: DeckDiscEntry;
  idx: number;
  tone: string;
  sourceReleaseGroupId: string;
  onUngroup: (jobId: number) => void;
}

function FannedDiscCard({ disc, idx, tone, sourceReleaseGroupId, onUngroup }: FannedDiscCardProps) {
  const dragData: FannedDiscDragData = {
    kind: "fanned-disc",
    jobId: disc.job_id,
    sourceReleaseGroupId,
  };
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `fanned-disc-${disc.job_id}`,
    data: dragData,
  });

  const dragStyle: React.CSSProperties = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`, zIndex: 50 }
    : {};

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      style={{
        width: 180,
        minHeight: 220,
        padding: 12,
        border: `1px solid ${tone}88`,
        background: sv.bg1,
        fontFamily: sv.mono,
        // Static rotation only when not being dragged; @dnd-kit transform takes over during drag.
        transform: isDragging ? dragStyle.transform : `rotate(${ROTATIONS[idx % ROTATIONS.length]}deg)`,
        transition: isDragging ? "none" : "transform 200ms ease-out",
        position: "relative",
        cursor: isDragging ? "grabbing" : "grab",
        opacity: isDragging ? 0.55 : 1,
        zIndex: dragStyle.zIndex,
      }}
      data-disc-job-id={disc.job_id}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ color: tone, fontWeight: 700, fontSize: 13 }}>
          Disc {disc.disc_number}
        </div>
        <span
          style={{
            color: disc.export_status === "submitted" ? sv.cyan : sv.green,
            fontSize: 8,
            border: `1px solid currentColor`,
            padding: "1px 5px",
            letterSpacing: "0.16em",
            textTransform: "uppercase",
          }}
        >
          {disc.export_status}
        </span>
      </div>
      <div
        style={{
          color: sv.inkFaint,
          fontSize: 9,
          marginTop: 3,
          wordBreak: "break-all",
        }}
      >
        {disc.content_hash ? disc.content_hash.slice(0, 12) + "…" : "—"}
      </div>

      <div style={{ marginTop: 12, fontSize: 10, color: sv.inkDim, lineHeight: 1.6 }}>
        <Row k="Titles" v={`${disc.matched_count}/${disc.title_count}`} />
        {disc.episode_range && <Row k="Range" v={disc.episode_range} />}
        <Row k="Runtime" v={formatRuntime(disc.runtime_seconds)} />
        {disc.has_extras && <Row k="" v="+ extras" extra />}
      </div>

      <div style={{ position: "absolute", bottom: 10, left: 12, right: 12 }}>
        <SvActionButton
          tone="neutral"
          size="sm"
          onClick={() => onUngroup(disc.job_id)}
        >
          <X size={10} /> Ungroup
        </SvActionButton>
      </div>
    </div>
  );
}

function Row({ k, v, extra }: { k: string; v: string; extra?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between" }}>
      <span style={{ color: sv.inkFaint, textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 9 }}>
        {k}
      </span>
      <span style={{ color: extra ? sv.amber : sv.ink }}>{v}</span>
    </div>
  );
}

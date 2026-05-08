import { useState } from "react";
import { motion } from "motion/react";
import { Search, ExternalLink, Check } from "lucide-react";
import { sv, SvActionButton, SvLabel } from "../../app/components/synapse";
import type { Deck } from "./types";

interface EnhancePanelProps {
  deck: Deck;
  onClose: () => void;
  onSaved: () => void;
}

interface UpcLookupResponse {
  success?: boolean;
  product_title?: string;
  brand?: string;
  asins?: string[];
  images?: string[];
  match_confidence?: number;
}

/**
 * Slim Enhance flow. F4 will wire UPC → /upc-lookup → auto-attach ASIN +
 * release_date + cover. For now this renders the input + a "click through to
 * TheDiscDB" escape hatch so the deck UI is functional end-to-end.
 */
export function EnhancePanel({ deck, onClose, onSaved }: EnhancePanelProps) {
  const tone = deck.content_type === "movie" ? sv.magenta : sv.cyan;
  const [upc, setUpc] = useState(deck.upc_code ?? "");
  const [lookup, setLookup] = useState<UpcLookupResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [coverError, setCoverError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Use the first disc as the lookup target — UPC enrichment in B3
  // will spread to the entire group via release_group_id on the backend side.
  const anchorJobId = deck.discs[0]?.job_id;
  const submittedDisc = deck.discs.find((d) => d.contribute_url);

  const handleLookup = async () => {
    if (!upc.trim() || !anchorJobId) return;
    setError(null);
    setLookup(null);
    setSaving(true);
    try {
      const res = await fetch(`/api/contributions/${anchorJobId}/upc-lookup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upc_code: upc.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || "UPC lookup failed");
      } else if (!data.success) {
        setError("No product found for that UPC");
      } else {
        setLookup(data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    if (!anchorJobId) return;
    setSaving(true);
    setError(null);
    setCoverError(null);
    try {
      const body: Record<string, string | undefined> = { upc_code: upc.trim() };
      // F4 will move ASIN + release_date selection into the backend; for
      // now we send what the lookup produced so the saved record is complete.
      if (lookup?.asins && lookup.asins.length > 0) body.asin = lookup.asins[0];
      const res = await fetch(`/api/contributions/${anchorJobId}/enhance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Save failed");
        return;
      }
      setSaved(true);
      // Best-effort cover fetch using the first image returned by lookup.
      // Failures are surfaced inline but do not block save success.
      if (lookup?.images && lookup.images.length > 0) {
        try {
          const coverRes = await fetch(`/api/contributions/${anchorJobId}/fetch-cover`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_url: lookup.images[0] }),
          });
          if (!coverRes.ok) {
            const coverBody = await coverRes
              .json()
              .catch(() => ({ detail: `HTTP ${coverRes.status}` }));
            setCoverError(coverBody.detail || "Cover fetch failed");
          }
        } catch (e) {
          setCoverError(e instanceof Error ? e.message : "Cover fetch failed");
        }
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      style={{ overflow: "hidden", marginTop: 16 }}
    >
      <div
        style={{
          padding: 16,
          background: `linear-gradient(180deg, ${sv.bg1} 0%, ${sv.bg0} 100%)`,
          border: `1px solid ${tone}55`,
          fontFamily: sv.mono,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <SvLabel size={10}>Enhance Submission</SvLabel>
          {submittedDisc?.contribute_url && (
            <a
              href={submittedDisc.contribute_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: sv.cyan,
                fontSize: 10,
                letterSpacing: "0.06em",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                textDecoration: "none",
              }}
            >
              Continue on TheDiscDB <ExternalLink size={11} />
            </a>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            value={upc}
            onChange={(e) => setUpc(e.target.value)}
            placeholder="UPC barcode (e.g. 043996634404)"
            style={{
              flex: 1,
              padding: "6px 10px",
              background: sv.bg0,
              border: `1px solid ${sv.line}`,
              color: sv.ink,
              fontFamily: sv.mono,
              fontSize: 12,
              outline: "none",
            }}
          />
          <SvActionButton tone="cyan" size="sm" onClick={handleLookup} disabled={saving || !upc.trim()}>
            <Search size={11} /> Lookup
          </SvActionButton>
        </div>

        {lookup && (
          <div
            style={{
              padding: 10,
              border: `1px solid ${sv.lineMid}`,
              background: sv.bg0,
              display: "flex",
              gap: 12,
              alignItems: "center",
            }}
          >
            {lookup.images && lookup.images.length > 0 && (
              <img
                src={lookup.images[0]}
                alt="Cover preview"
                style={{ width: 60, height: 90, objectFit: "cover", border: `1px solid ${sv.line}` }}
              />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: sv.ink, fontSize: 12, fontWeight: 600 }}>
                {lookup.product_title ?? "Untitled product"}
              </div>
              {lookup.brand && (
                <div style={{ color: sv.inkDim, fontSize: 10, marginTop: 2 }}>{lookup.brand}</div>
              )}
              <div style={{ color: sv.inkFaint, fontSize: 10, marginTop: 4 }}>
                {lookup.asins && lookup.asins.length > 0 ? `ASIN ${lookup.asins[0]}` : "No ASIN"}
                {lookup.match_confidence != null && ` · ${Math.round(lookup.match_confidence * 100)}% match`}
              </div>
            </div>
            <SvActionButton tone="cyan" size="sm" onClick={handleSave} disabled={saving || saved}>
              {saved ? (<><Check size={11} /> Saved</>) : "Save & Attach"}
            </SvActionButton>
          </div>
        )}

        {error && (
          <div style={{ color: sv.red, fontSize: 11 }}>{error}</div>
        )}

        {coverError && (
          <div style={{ color: sv.red, fontSize: 11 }}>Cover fetch failed: {coverError}</div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <SvActionButton tone="neutral" size="sm" onClick={onClose}>
            Close
          </SvActionButton>
        </div>
      </div>
    </motion.div>
  );
}

import type { CSSProperties } from "react";
import { Link, useLocation } from "react-router-dom";
import { Settings } from "lucide-react";
import { sv } from "./tokens";
import { SvMark } from "./SvMark";
import { SvBadge } from "./SvBadge";

interface NavItem {
  label: string;
  to: string;
  /** Numeric badge (yellow). Falsy = no badge. */
  badge?: number;
  /** Show the route in the nav. Default true. */
  show?: boolean;
}

interface Props {
  isConnected: boolean;
  /** App version, shown beneath the wordmark. */
  version: string;
  /** Render a [MOCK] tag next to the LIVE pill. */
  devMode?: boolean;
  navItems: NavItem[];
  onSettingsClick: () => void;
}

/**
 * Top bar — fixed-height (76px) site header.
 * Layout: [Mark + wordmark + sub-line] [center nav] [LIVE pill + settings].
 *
 * Underline + glow on the active nav item is the recurring "you are here"
 * signal throughout the app.
 */
export function SvTopBar({
  isConnected,
  version,
  devMode = false,
  navItems,
  onSettingsClick,
}: Props) {
  const location = useLocation();

  const root: CSSProperties = {
    height: 76,
    padding: "0 28px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottom: `1px solid ${sv.line}`,
    background: "linear-gradient(180deg, rgba(18,24,39,0.7), transparent)",
    backdropFilter: "blur(8px)",
    position: "sticky",
    top: 0,
    zIndex: 20,
  };

  return (
    <header style={root} data-testid="sv-topbar">
      {/* Left: Mark + wordmark */}
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <SvMark size={38} />
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span
            style={{
              fontFamily: sv.display,
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: "0.18em",
              color: sv.cyanHi,
              textShadow: `0 0 12px ${sv.cyan}55`,
            }}
          >
            ENGRAM
          </span>
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 9,
              fontWeight: 400,
              letterSpacing: "0.24em",
              color: sv.inkFaint,
              textTransform: "uppercase",
            }}
          >
            <span style={{ color: sv.cyan }}>›</span> MEMORY · ARCHIVAL · v{version}
          </span>
        </div>
      </div>

      {/* Center: nav */}
      <nav style={{ display: "flex", alignItems: "center", gap: 4 }} data-testid="sv-topnav">
        {navItems
          .filter((it) => it.show !== false)
          .map((item) => {
            const active =
              item.to === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(item.to);
            return (
              <NavTab key={item.to} item={item} active={active} />
            );
          })}
      </nav>

      {/* Right: LIVE pill + settings */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <SvBadge state={isConnected ? "live" : "queued"} dot>
          {isConnected ? "LIVE" : "OFFLINE"}
        </SvBadge>
        {devMode && (
          <span
            style={{
              fontFamily: sv.mono,
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: "0.2em",
              color: sv.yellow,
            }}
            data-testid="sv-mock-tag"
          >
            [MOCK]
          </span>
        )}
        <div style={{ width: 1, height: 24, background: sv.line }} />
        <button
          onClick={onSettingsClick}
          aria-label="Settings"
          title="Settings"
          data-testid="sv-settings-btn"
          style={{
            background: "transparent",
            border: `1px solid ${sv.line}`,
            color: sv.inkDim,
            padding: "8px 10px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            transition: "all 0.18s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = sv.lineHi;
            e.currentTarget.style.color = sv.cyan;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = sv.line;
            e.currentTarget.style.color = sv.inkDim;
          }}
        >
          <Settings size={14} />
        </button>
      </div>
    </header>
  );
}

function NavTab({ item, active }: { item: NavItem; active: boolean }) {
  const wrap: CSSProperties = {
    position: "relative",
    padding: "12px 18px",
    fontFamily: sv.mono,
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: "0.20em",
    textTransform: "uppercase",
    color: active ? sv.cyanHi : sv.inkDim,
    textDecoration: "none",
    transition: "color 0.18s",
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
  };
  const underline: CSSProperties = {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 0,
    height: 2,
    background: sv.cyan,
    boxShadow: `0 0 8px ${sv.cyan}88`,
    transition: "opacity 0.18s",
    opacity: active ? 1 : 0,
  };

  return (
    <Link
      to={item.to}
      style={wrap}
      data-testid={`sv-nav-${item.label.toLowerCase()}`}
      data-active={active ? "true" : "false"}
    >
      <span>{item.label}</span>
      {item.badge != null && item.badge > 0 && (
        <span
          style={{
            fontFamily: sv.mono,
            fontSize: 10,
            fontWeight: 700,
            color: sv.bg0,
            background: sv.yellow,
            padding: "1px 6px",
            letterSpacing: "0.05em",
          }}
        >
          {item.badge}
        </span>
      )}
      <span style={underline} />
    </Link>
  );
}

import type { CSSProperties } from "react";
import { Link, useLocation } from "react-router-dom";
import type { NavItem } from "../../navigation";
import { IcoSettings, IcoLibrary, IcoDisc } from "../icons";
import { sv } from "./tokens";
import { SvMark } from "./SvMark";
import { Wordmark } from "./Wordmark";
import { SvBadge } from "./SvBadge";

interface Props {
  isConnected: boolean;
  /** App version, shown beneath the wordmark. */
  version: string;
  /** Render a [MOCK] tag next to the LIVE pill. */
  devMode?: boolean;
  navItems: NavItem[];
  onSettingsClick: () => void;
  onImportClick: () => void;
  onManualClick?: () => void;
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
  onImportClick,
  onManualClick,
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
    WebkitBackdropFilter: "blur(8px)",
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
          <Wordmark
            size={22}
            color={sv.cyanHi}
            letterSpacing="0.18em"
            style={{ textShadow: `0 0 12px ${sv.cyan}55` }}
          />
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
            const matchPath = item.activeWhen ?? item.to;
            const active =
              matchPath === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(matchPath);
            return (
              <NavTab key={item.label} item={item} active={active} />
            );
          })}
      </nav>

      {/* Right: LIVE pill + settings */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button
          onClick={onManualClick}
          aria-label="Manual disc identity"
          title="Enter disc metadata manually for the next disc"
          data-testid="sv-manual-btn"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            border: `1px solid ${sv.magenta}`,
            color: sv.magenta,
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.16em",
            padding: "7px 12px",
            cursor: "pointer",
            transition: "all 0.18s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${sv.magenta}1f`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IcoDisc size={14} />
          MANUAL
        </button>
        <button
          onClick={onImportClick}
          aria-label="Import media"
          title="Import media"
          data-testid="sv-import-btn"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            border: `1px solid ${sv.cyan}`,
            color: sv.cyan,
            fontFamily: sv.mono,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.16em",
            padding: "7px 12px",
            cursor: "pointer",
            transition: "all 0.18s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${sv.cyan}1f`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IcoLibrary size={14} />
          IMPORT
        </button>
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
          <IcoSettings size={14} />
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

  const content = (
    <>
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
    </>
  );

  if (item.disabled) {
    // Disabled-link pattern: aria-disabled is only announced when the element
    // has a semantic role, and the title tooltip is mouse-only — so carry the
    // role explicitly and fold the reason into the accessible name.
    return (
      <span
        role="link"
        aria-disabled="true"
        aria-label={
          item.disabledHint ? `${item.label} — ${item.disabledHint}` : item.label
        }
        style={{ ...wrap, color: sv.inkFaint, cursor: "default" }}
        data-testid={`sv-nav-${item.label.toLowerCase()}`}
        data-active="false"
        title={item.disabledHint}
      >
        {content}
      </span>
    );
  }

  return (
    <Link
      to={item.to}
      style={wrap}
      data-testid={`sv-nav-${item.label.toLowerCase()}`}
      data-active={active ? "true" : "false"}
    >
      {content}
    </Link>
  );
}

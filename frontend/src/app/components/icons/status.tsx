import { sv } from "../synapse/tokens";
import { Ico, type IconProps } from "./Ico";

/* 8 status icons — color matches the semantic state palette. */

export function IcoIdle(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Idle">
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="12" x2="12" y2="6" />
      <line x1="12" y1="12" x2="16" y2="14" />
    </Ico>
  );
}

export function IcoScan(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Scanning">
      <circle cx="11" cy="11" r="6" />
      <line x1="11" y1="5" x2="11" y2="17" opacity="0.5" />
      <line x1="5" y1="11" x2="17" y2="11" opacity="0.5" />
      <line x1="15.5" y1="15.5" x2="20" y2="20" />
    </Ico>
  );
}

export function IcoRipping(p: IconProps) {
  const accent = p.accent ?? sv.magenta;
  return (
    <Ico {...p} defaultTitle="Ripping">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" opacity="0.5" />
      <circle cx="12" cy="12" r="1.5" fill={accent} stroke="none" />
      <line x1="12" y1="12" x2="21" y2="12" stroke={accent} />
    </Ico>
  );
}

export function IcoMatching(p: IconProps) {
  const accent = p.accent ?? sv.amber;
  return (
    <Ico {...p} defaultTitle="Matching">
      <path d="M 4 8 L 9 8 L 11 11" />
      <path d="M 20 8 L 15 8 L 13 11" />
      <path d="M 4 16 L 9 16 L 11 13" />
      <path d="M 20 16 L 15 16 L 13 13" />
      <circle cx="12" cy="12" r="1.5" fill={accent} stroke="none" />
    </Ico>
  );
}

export function IcoComplete(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Complete">
      <circle cx="12" cy="12" r="9" />
      <path d="M 8 12 L 11 15 L 16 9" />
    </Ico>
  );
}

export function IcoPaused(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Paused">
      <circle cx="12" cy="12" r="9" />
      <line x1="10" y1="9" x2="10" y2="15" />
      <line x1="14" y1="9" x2="14" y2="15" />
    </Ico>
  );
}

export function IcoQueued(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Queued">
      <circle cx="12" cy="12" r="9" strokeDasharray="3 3" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </Ico>
  );
}

export function IcoError(p: IconProps) {
  const color = p.color ?? sv.red;
  return (
    <Ico {...p} color={color} defaultTitle="Error">
      <path d="M 12 3 L 22 20 L 2 20 Z" />
      <line x1="12" y1="10" x2="12" y2="14" />
      <circle cx="12" cy="17" r="0.5" fill={color} stroke="none" />
    </Ico>
  );
}

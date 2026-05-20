import { Ico, type IconProps } from "./Ico";

/* 14 action + navigation icons — cyan on hover, inkDim at rest. */

export function IcoPlay(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Play">
      <path d="M 7 5 L 19 12 L 7 19 Z" />
    </Ico>
  );
}

export function IcoPause(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Pause">
      <rect x="7" y="5" width="3" height="14" />
      <rect x="14" y="5" width="3" height="14" />
    </Ico>
  );
}

export function IcoCancel(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Cancel">
      <circle cx="12" cy="12" r="9" />
      <line x1="8" y1="8" x2="16" y2="16" />
      <line x1="16" y1="8" x2="8" y2="16" />
    </Ico>
  );
}

export function IcoRetry(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Retry">
      <path d="M 4 12 A 8 8 0 1 1 6.5 17.7" />
      <polyline points="3 17 6.5 17.7 7.2 14.2" />
    </Ico>
  );
}

export function IcoEject(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Eject">
      <path d="M 6 11 L 12 4 L 18 11 Z" />
      <line x1="6" y1="19" x2="18" y2="19" />
    </Ico>
  );
}

export function IcoSettings(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Settings">
      <circle cx="12" cy="12" r="3" />
      <path d="M 12 2 L 12 5 M 12 19 L 12 22 M 4.93 4.93 L 7.05 7.05 M 16.95 16.95 L 19.07 19.07 M 2 12 L 5 12 M 19 12 L 22 12 M 4.93 19.07 L 7.05 16.95 M 16.95 7.05 L 19.07 4.93" />
    </Ico>
  );
}

export function IcoHistory(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="History">
      <path d="M 3 12 A 9 9 0 1 0 6 5.5" />
      <polyline points="3 5 3 9 7 9" />
      <polyline points="12 7 12 12 15.5 14" />
    </Ico>
  );
}

export function IcoReview(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Review">
      <rect x="4" y="3" width="16" height="18" rx="1" />
      <line x1="8" y1="8" x2="16" y2="8" />
      <line x1="8" y1="12" x2="16" y2="12" />
      <line x1="8" y1="16" x2="13" y2="16" />
      <circle
        cx="17"
        cy="16"
        r="2"
        fill="currentColor"
        stroke="none"
        opacity="0.8"
      />
    </Ico>
  );
}

export function IcoDashboard(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Dashboard">
      <rect x="3" y="3" width="8" height="10" />
      <rect x="13" y="3" width="8" height="5" />
      <rect x="13" y="10" width="8" height="11" />
      <rect x="3" y="15" width="8" height="6" />
    </Ico>
  );
}

export function IcoSearch(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Search">
      <circle cx="10" cy="10" r="6" />
      <line x1="14.5" y1="14.5" x2="20" y2="20" />
    </Ico>
  );
}

export function IcoFilter(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Filter">
      <path d="M 3 5 L 21 5 L 14 13 L 14 20 L 10 18 L 10 13 Z" />
    </Ico>
  );
}

export function IcoMore(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="More">
      <circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </Ico>
  );
}

export function IcoConfidence(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Confidence">
      <path d="M 4 14 L 9 9 L 13 13 L 20 6" />
      <polyline points="15 6 20 6 20 11" />
    </Ico>
  );
}

export function IcoBytes(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Bytes">
      <rect x="3" y="9" width="6" height="6" />
      <rect x="11" y="9" width="6" height="6" />
      <rect x="19" y="9" width="2" height="6" />
      <line x1="3" y1="6" x2="21" y2="6" opacity="0.5" />
    </Ico>
  );
}

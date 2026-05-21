import { sv } from "../synapse/tokens";
import { Ico, type IconProps } from "./Ico";

/* 8 media-type icons — cyan by default, surveillance-style glyphs. */

export function IcoDisc(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Disc">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
      <path d="M 12 3 A 9 9 0 0 1 21 12" strokeWidth="2.4" opacity="0.6" />
    </Ico>
  );
}

export function IcoBluRay(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Blu-ray">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" />
      <text
        x="12"
        y="20.5"
        fontFamily={sv.mono}
        fontSize="3.4"
        fontWeight="700"
        textAnchor="middle"
        fill="currentColor"
        stroke="none"
        letterSpacing="0.05em"
      >
        BD
      </text>
    </Ico>
  );
}

export function IcoDvd(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="DVD">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" />
      <text
        x="12"
        y="20.5"
        fontFamily={sv.mono}
        fontSize="3"
        fontWeight="700"
        textAnchor="middle"
        fill="currentColor"
        stroke="none"
        letterSpacing="0.05em"
      >
        DVD
      </text>
    </Ico>
  );
}

export function IcoTv(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="TV series">
      <rect x="3" y="6" width="18" height="12" rx="1" />
      <line x1="8" y1="20.5" x2="16" y2="20.5" />
      <path d="M 8 10 L 11 12 L 8 14 Z" fill="currentColor" />
    </Ico>
  );
}

export function IcoMovie(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Movie">
      <rect x="3" y="5" width="18" height="14" rx="1" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <circle cx="6.5" cy="7" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="10" cy="7" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="13.5" cy="7" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="17" cy="7" r="0.6" fill="currentColor" stroke="none" />
      <path d="M 10 12 L 15 14.5 L 10 17 Z" fill="currentColor" />
    </Ico>
  );
}

export function IcoEpisode(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Episode">
      <rect x="3" y="5" width="18" height="14" rx="1" />
      <line x1="3" y1="11" x2="21" y2="11" opacity="0.4" />
      <line x1="3" y1="15" x2="21" y2="15" opacity="0.4" />
      <line x1="9" y1="5" x2="9" y2="19" opacity="0.4" />
      <line x1="15" y1="5" x2="15" y2="19" opacity="0.4" />
      <rect
        x="9"
        y="11"
        width="6"
        height="4"
        fill="currentColor"
        stroke="none"
        opacity="0.9"
      />
    </Ico>
  );
}

export function IcoDrive(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Drive">
      <rect x="3" y="6" width="18" height="12" rx="1" />
      <line x1="3" y1="14" x2="21" y2="14" />
      <circle cx="7" cy="16.5" r="0.8" fill="currentColor" stroke="none" />
      <line x1="11" y1="16.5" x2="18" y2="16.5" opacity="0.4" />
      <line x1="6" y1="9.5" x2="14" y2="9.5" opacity="0.4" />
    </Ico>
  );
}

export function IcoLibrary(p: IconProps) {
  return (
    <Ico {...p} defaultTitle="Library">
      <rect x="4" y="4" width="3" height="16" />
      <rect x="9" y="6" width="3" height="14" />
      <rect x="14" y="3" width="3" height="17" />
      <rect x="19" y="8" width="2" height="12" />
    </Ico>
  );
}

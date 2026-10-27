// The star map's visual language (docs/question/star-map.md) as numbers: the
// sky tokens, the categorical palette, star brightness and size, and the track
// layout used before the graph exists. Shared by the 2D and 3D views.
import type { PlanQuery } from '../types';
import { screening, type Star } from '../run';

/** oklch → `#rrggbb` (or `#rrggbbaa` with `alpha` < 1), clipped to sRGB. */
export function oklch(l: number, c: number, h: number, alpha = 1): string {
  const hr = (h * Math.PI) / 180;
  const a = c * Math.cos(hr);
  const b = c * Math.sin(hr);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
  const hex = (v: number) =>
    Math.round(Math.min(1, Math.max(0, v)) * 255)
      .toString(16)
      .padStart(2, '0');
  const gamma = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * Math.max(v, 0) ** (1 / 2.4) - 0.055);
  return `#${lin.map((v) => hex(gamma(v))).join('')}${alpha < 1 ? hex(alpha) : ''}`;
}

export const SKY = {
  bg: oklch(0.16, 0.025, 265),
  dust: oklch(0.85, 0.02, 85, 0.35),
  keyword: oklch(0.78, 0.02, 250),
  edge: oklch(0.75, 0.02, 250),
  label: oklch(0.93, 0.01, 90),
  mark: oklch(0.97, 0.01, 90),
  other: oklch(0.62, 0.02, 250),
};

/** Ten hues 36° apart from 30°, lightness and chroma alternating in their bands. */
const HUES = Array.from({ length: 10 }, (_, i) =>
  oklch(i % 2 ? 0.82 : 0.74, i % 3 === 0 ? 0.15 : i % 3 === 1 ? 0.12 : 0.13, 30 + 36 * i),
);
/** The order hues are handed out in, so the first few never neighbour. */
const ORDER = [0, 5, 2, 7, 4, 9, 1, 6, 3, 8];

/** The hue of the `n`-th query or community (0-based); from the tenth on they share `other`. */
export function hue(n: number): string {
  return n >= 0 && n < 9 ? HUES[ORDER[n]!]! : SKY.other;
}

/** Brightness 0..1 from citations, plus a step per repeat hit (star-map.md 1.1). */
export function brightness(citations: number, maxCitations: number, repeats: number): number {
  const base = maxCitations > 0 ? Math.log10(1 + citations) / Math.log10(1 + maxCitations) : 0;
  return Math.min(1, 0.45 + 0.55 * base + Math.min(0.18, 0.06 * repeats));
}

/** The look of one star before the graph exists. */
export interface StarLook {
  /** Fill color; transparent for a hollow (excluded) star. */
  color: string;
  /** Outline color; equal to `color` when there is none. */
  border: string;
  size: number;
  /** Drawn order: excluded stars sink under the rest. */
  zIndex: number;
  hollow: boolean;
  dashed: boolean;
  overridden: boolean;
}

export const STAR_PX = 4;

/** Radius in screen pixels: 2.5 for an uncited paper up to 6 for the most cited;
 * excluded stars stay small so they read as background. */
export function starSize(citations: number, maxCitations: number, excluded = false): number {
  if (excluded) return 2.5;
  const base = maxCitations > 0 ? Math.log10(1 + citations) / Math.log10(1 + maxCitations) : 0;
  return 2.5 + 3.5 * base;
}

export function starLook(star: Star, queryHue: string, maxCitations: number, selected = false): StarLook {
  const state = screening(star);
  const b = brightness(star.paper.cited_by_count ?? 0, maxCitations, star.repeats);
  const level = state === 'candidate' ? 0.7 * b : state === 'exclude' ? 0.25 : b;
  const overridden = star.overrides.some((o) => o.decision !== null);
  const color = withAlpha(queryHue, level);
  const look: StarLook = {
    color: state === 'exclude' ? withAlpha(queryHue, 0) : color,
    border: state === 'exclude' ? withAlpha(desaturate(queryHue), 0.25) : color,
    size: starSize(star.paper.cited_by_count ?? 0, maxCitations, state === 'exclude'),
    zIndex: state === 'exclude' ? 0 : 1,
    hollow: state === 'exclude',
    dashed: state === 'unscreened',
    overridden,
  };
  if (selected) {
    look.border = SKY.mark;
    look.size = Math.max(look.size, STAR_PX) * 1.6;
    look.zIndex = 2;
  }
  return look;
}

/** `#rrggbbaa` → the opaque color it shows over the sky. The WebGL blend adds a
 * color's full brightness whatever its alpha, so the map only gets opaque ones. */
export function flatten(hex: string, alpha = 1): string {
  const a = (hex.length > 7 ? parseInt(hex.slice(7, 9), 16) / 255 : 1) * alpha;
  if (a >= 1 && hex.length === 7) return hex;
  const channel = (i: number) => {
    const v = parseInt(hex.slice(i, i + 2), 16);
    const bg = parseInt(SKY.bg.slice(i, i + 2), 16);
    return Math.round(bg + (v - bg) * a)
      .toString(16)
      .padStart(2, '0');
  };
  return `#${channel(1)}${channel(3)}${channel(5)}`;
}

function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
    .toString(16)
    .padStart(2, '0');
  return `${hex.slice(0, 7)}${a}`;
}

function desaturate(hex: string): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const grey = Math.round(0.3 * r! + 0.59 * g! + 0.11 * b!);
  const mix = (v: number) =>
    Math.round((v + grey) / 2)
      .toString(16)
      .padStart(2, '0');
  return `#${mix(r!)}${mix(g!)}${mix(b!)}`;
}

// --- tracks: the arms of a spiral galaxy -------------------------------------

/** One query's arm: it leaves the core at `angle` and winds out by `twist`. */
export interface Track {
  queryId: string;
  /** Hue index: the query's place in the plan. */
  index: number;
  /** Radians where the arm leaves the core. */
  angle: number;
  /** Radians the arm turns between `inner` and `outer`. */
  twist: number;
  inner: number;
  outer: number;
}

/** The core's radius: where rank 1 of every query starts. */
export const CORE = 50;
/** The longest arm, for the highest priority; lower priorities are shorter. */
export const ARM = 520;
const ARM_STEP = 30;
const TWIST = 2.4;
/** The rank an arm ends at when the query has no `limit`. */
export const DEFAULT_RANKS = 100;

/** One spiral arm per query, evenly around the core in plan order; a higher
 * priority makes a longer arm. */
export function tracks(queries: PlanQuery[]): Map<string, Track> {
  const priorities = [...new Set(queries.map((q) => q.priority))].sort((a, b) => a - b);
  const out = new Map<string, Track>();
  queries.forEach((q, i) => {
    out.set(q.id, {
      queryId: q.id,
      index: i,
      angle: -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, queries.length),
      twist: TWIST,
      inner: CORE,
      outer: Math.max(ARM * 0.6, ARM - ARM_STEP * priorities.indexOf(q.priority)),
    });
  });
  return out;
}

/** A point along an arm, `t` from 0 (the core) to 1 (its tip), in canvas
 * orientation (y down); `onTrack` flips it for the map. */
export function armPoint(track: Track, t: number): { x: number; y: number; r: number } {
  const r = track.inner + (track.outer - track.inner) * t ** 0.85;
  const a = track.angle + track.twist * t;
  return { x: r * Math.cos(a), y: r * Math.sin(a), r };
}

/** A stable number in [0, 1) from a string. */
export function jitter(text: string, salt = 0): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 100000) / 100000;
}

/** Where a star sits on its arm: out from the core by rank, scattered across
 * an arm that widens towards its tip, as the stars of a galaxy are. */
export function onTrack(
  track: Track,
  rank: number,
  key: string,
  ranks = DEFAULT_RANKS,
): { x: number; y: number } {
  const t = Math.min(1, (rank - 1) / Math.max(1, ranks - 1));
  const p = armPoint(track, Math.min(1, Math.max(0, t + (jitter(key, 1) - 0.5) * 0.06)));
  // Two jitters summed lean towards the middle of the arm.
  const across = jitter(key, 2) + jitter(key, 5) - 1;
  const width = 14 + 46 * t;
  const a = Math.atan2(p.y, p.x);
  const x = p.x - Math.sin(a) * across * width;
  const y = p.y + Math.cos(a) * across * width;
  // Screen y grows downwards; sigma's grows upwards, so flip here once.
  return { x, y: -y };
}

/** A paper several queries found sits between their arms, pulled towards the
 * core: the more queries agree on it, the closer to the centre it shines. */
export function between(points: { x: number; y: number }[]): { x: number; y: number } {
  const n = points.length;
  const x = points.reduce((s, p) => s + p.x, 0) / n;
  const y = points.reduce((s, p) => s + p.y, 0) / n;
  const pull = 1 / (1 + 0.45 * (n - 1));
  return { x: x * pull, y: y * pull };
}

/** Where an expanded star settles: beside its parent. */
export function besideParent(parent: { x: number; y: number }, key: string): { x: number; y: number } {
  const angle = jitter(key, 3) * 2 * Math.PI;
  const r = 14 + jitter(key, 4) * 18;
  return { x: parent.x + r * Math.cos(angle), y: parent.y + r * Math.sin(angle) };
}

// What the 2D map draws for a run state, as plain node attributes, so it can be
// checked without WebGL. Before the graph exists every star sits on the track
// of the query that found it first; an expanded star sits beside its parent.
import { counts, screening, type RunState, type Star } from '../run';
import { besideParent, between, flatten, hue, onTrack, starLook, tracks, type Track } from './sky';

export interface NodeAttrs {
  x: number;
  y: number;
  size: number;
  color: string;
  borderColor: string;
  zIndex: number;
  label: string | null;
  forceLabel: boolean;
  /** For the legend and the tooltip. */
  state: ReturnType<typeof screening>;
  hollow: boolean;
  dashed: boolean;
  overridden: boolean;
  /** The queries that found it, for focusing one track. */
  queries: string[];
  /** Its track's hue, for the glow. */
  hue: string;
  /** Halo strength 0..1: included stars glow by citations (by PageRank once the
   * graph is built), others not at all. */
  glow: number;
  /** Once the graph is built (graphScene.ts): its node id, whether this is a paper
   * or a keyword, and its rings: 1 for a hub, 2 for a bridge. */
  graphId?: string;
  kind?: 'paper' | 'keyword';
  ring?: number;
  /** A keyword: how many shown papers carry it. */
  papers?: number;
}

export interface Scene {
  nodes: Map<string, NodeAttrs>;
  tracks: Track[];
  /** Track id → hue, for the arcs and the legend. */
  hues: Map<string, string>;
  /** Track id → whether the query failed. */
  failed: Set<string>;
  radius: number;
  /** The part of the sky the camera frames, when not the circle of `radius`. */
  bbox?: { x: [number, number]; y: [number, number] };
  /** Included stars that glow, brightest first, at most `HALOS`. */
  halos: string[];
}

/** Halos drawn at most: the brightest included stars. */
export const HALOS = 160;

/** A short label: the title, or first author + year for long titles. */
export function shortTitle(star: Star): string {
  const { title, authors, year } = star.paper;
  if (title.length <= 28) return title;
  const who = authors?.[0]?.split(/\s+/).pop();
  return who ? `${who}${year ? ` ${year}` : ''}` : `${title.slice(0, 27)}…`;
}

export function scene(state: RunState, selected: string | null, showExcluded = false): Scene {
  const queries = state.plan?.queries ?? [];
  const byId = tracks(queries);
  const hues = new Map([...byId.values()].map((t) => [t.queryId, hue(t.index)]));
  // A track spreads over the ranks its query has returned so far (at least
  // 20): a query that stops short of its limit still fills its arc, and one
  // that returns more than its limit does not pile up at the end.
  const seen = new Map<string, number>();
  for (const star of state.stars.values())
    for (const f of star.found)
      if ('query_id' in f) seen.set(f.query_id, Math.max(seen.get(f.query_id) ?? 0, f.rank));
  const ranks = new Map(queries.map((q) => [q.id, Math.max(20, seen.get(q.id) ?? 0)]));
  let maxCitations = 0;
  for (const star of state.stars.values())
    maxCitations = Math.max(maxCitations, star.paper.cited_by_count ?? 0);

  const nodes = new Map<string, NodeAttrs>();
  const place = (star: Star, depth = 0): { x: number; y: number; hue: string } => {
    // Every query that found it pulls on it; its hue stays the first one's.
    const seen = new Set<string>();
    const pulls: { x: number; y: number }[] = [];
    let first: string | null = null;
    for (const f of star.found) {
      if (!('query_id' in f) || seen.has(f.query_id)) continue;
      const track = byId.get(f.query_id);
      if (!track) continue;
      seen.add(f.query_id);
      first ??= f.query_id;
      pulls.push(onTrack(track, f.rank, star.key, ranks.get(f.query_id)));
    }
    if (first) return { ...between(pulls), hue: hues.get(first)! };
    for (const f of star.found) {
      if (!('query_id' in f) && depth < 4) {
        const parent = state.stars.get(state.keyIndex.get(f.from_key) ?? f.from_key);
        if (parent) {
          const p = place(parent, depth + 1);
          return { ...besideParent(p, star.key), hue: p.hue };
        }
      }
    }
    return { x: 0, y: 0, hue: hue(99) };
  };
  for (const star of state.stars.values()) {
    const at = place(star);
    const isSelected = star.key === selected;
    const look = starLook(star, at.hue, maxCitations, isSelected);
    const state_ = screening(star);
    const cites = star.paper.cited_by_count ?? 0;
    const glow =
      state_ === 'include' && maxCitations > 0 ? Math.log10(1 + cites) / Math.log10(1 + maxCitations) : 0;
    nodes.set(star.key, {
      x: at.x,
      y: at.y,
      size: look.size,
      // Shown again, an excluded star is a dim solid dot.
      // Colors go to the map opaque, already mixed into the sky (see `flatten`):
      // a hollow star's fill is the sky itself.
      color: flatten(showExcluded && look.hollow ? `${look.border.slice(0, 7)}88` : look.color),
      borderColor: flatten(look.border),
      zIndex: look.zIndex,
      // Candidates and excluded stars carry no label (star-map.md 1.2).
      label: isSelected || state_ === 'include' || state_ === 'unscreened' ? shortTitle(star) : null,
      // Only the selected star forces its label; sigma's label grid picks the
      // rest from the biggest stars, so labels never pile up.
      forceLabel: isSelected,
      state: state_,
      hollow: look.hollow,
      dashed: look.dashed,
      overridden: look.overridden,
      queries: [...new Set(star.found.flatMap((f) => ('query_id' in f ? [f.query_id] : [])))],
      hue: at.hue,
      glow,
    });
  }
  const failed = new Set(state.problems.map((p) => p.query_id).filter((q): q is string => !!q));
  const outer = Math.max(0, ...[...byId.values()].map((t) => t.outer));
  const halos = [...nodes]
    .filter(([, n]) => n.glow > 0.05)
    .sort((a, b) => b[1].glow - a[1].glow)
    .slice(0, HALOS)
    .map(([k]) => k);
  return { nodes, tracks: [...byId.values()], hues, failed, radius: outer + 70, halos };
}

/** The number the search-animation check compares with the map: hits minus repeats. */
export function starCount(state: RunState): number {
  return counts(state).candidates;
}

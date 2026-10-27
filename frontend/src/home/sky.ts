// Drawing the universe on a 2D canvas: background dust with a little
// parallax, then each galaxy as a glow and its papers as stars where their
// forces settled them (field.ts), with the citation routes between them and
// the trunk lit, then the names and the amber pulse on galaxies that need you.
import type { Field } from './field';
import { starLabel, type FocusMap } from './focus';
import { rng, type Galaxy } from './galaxies';

export interface Camera {
  x: number;
  y: number;
  z: number;
}

const DUST = (() => {
  const r = rng(7);
  return Array.from({ length: 900 }, () => ({
    x: (r() - 0.5) * 2600,
    y: (r() - 0.5) * 1700,
    s: r() * 1.1 + 0.2,
    a: 0.15 + r() * 0.45,
    tw: r() * 6.28,
    d: 0.35 + r() * 0.5,
  }));
})();

/** The selection ring, in galaxy radii; every star sits inside it (field.ts RIM). */
export const RING = 1.18;

export const color = (g: Galaxy, l: number, a = 1) => `hsla(${g.hue} 70% ${l}% / ${a})`;

export interface Scene {
  galaxies: Galaxy[];
  cam: Camera;
  w: number;
  h: number;
  hover: Galaxy | null;
  selected: Galaxy | null;
  /** Galaxies with something for the person to do. */
  flagged: Set<string>;
  /** Each galaxy's settled papers, once loaded. */
  fields: Map<string, Field>;
  /** Labels under busy galaxies, e.g. "Searching…". */
  busyLabel: (g: Galaxy) => string;
  detail: (g: Galaxy) => string;
  reduce: boolean;
  /** The selected galaxy's map from its graph (focus.ts), once loaded. */
  focus?: FocusMap | null;
  /** The star of the focus map under the pointer. */
  hoverStar?: number | null;
  /** "{n} papers", for the sector labels. */
  papersLabel?: (n: number) => string;
  /** The name of the sector of papers in no (or a folded) community. */
  otherLabel?: string;
}

/** Where the zoom buttons end, from the canvas top. */
const ZOOM_BOTTOM = 200;

/** The selected galaxy turns into its map from this many pixels of radius. */
export const FOCUS_PX = 70;

/** Whether the selected galaxy is drawn as its map, and where. */
export function focusFrame(
  s: Pick<Scene, 'selected' | 'focus' | 'cam' | 'w' | 'h'>,
): { g: Galaxy; map: FocusMap; cx: number; cy: number; R: number } | null {
  const g = s.selected;
  if (!g || !s.focus) return null;
  const R = g.r * s.cam.z;
  if (R < FOCUS_PX) return null;
  const p = toScreen(s.cam, s.w, s.h, g.x, g.y);
  return { g, map: s.focus, cx: p.x, cy: p.y, R };
}

/** The focus map's star under a screen point, within a few pixels. */
export function pickStar(
  s: Pick<Scene, 'selected' | 'focus' | 'cam' | 'w' | 'h'>,
  px: number,
  py: number,
): number | null {
  const f = focusFrame(s);
  if (!f) return null;
  let best: number | null = null;
  let bd = 9;
  f.map.stars.forEach((st, i) => {
    const d = Math.hypot(f.cx + st.x * f.R - px, f.cy + st.y * f.R - py);
    if (d < bd) {
      best = i;
      bd = d;
    }
  });
  return best;
}

export const toScreen = (cam: Camera, w: number, h: number, x: number, y: number) => ({
  x: (x - cam.x) * cam.z + w / 2,
  y: (y - cam.y) * cam.z + h / 2,
});
export const toWorld = (cam: Camera, w: number, h: number, x: number, y: number) => ({
  x: (x - w / 2) / cam.z + cam.x,
  y: (y - h / 2) / cam.z + cam.y,
});

/** The camera that shows every galaxy. */
export function fitAll(galaxies: Galaxy[], w: number, h: number): Camera {
  if (!galaxies.length) return { x: 0, y: 0, z: 1 };
  const xs = galaxies.flatMap((g) => [g.x - g.r, g.x + g.r]);
  const ys = galaxies.flatMap((g) => [g.y - g.r, g.y + g.r]);
  const bw = Math.max(...xs) - Math.min(...xs);
  const bh = Math.max(...ys) - Math.min(...ys);
  // Screen room for the names under the outer galaxies and the title above.
  return {
    x: (Math.max(...xs) + Math.min(...xs)) / 2,
    y: (Math.max(...ys) + Math.min(...ys)) / 2 + 16,
    z: Math.max(0.3, Math.min((w - Math.min(220, w * 0.2)) / bw, (h - Math.min(150, h * 0.2)) / bh, 1.8)),
  };
}

/** A screen rectangle, e.g. the preview card over the canvas. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Room kept clear at the top for the title, and under a galaxy for its name. */
const TOP = 64;
const NAME = 40;
const PAD = 16;

/** The camera for a selection: the galaxy, its ring and its name as large as
 * they fit in the open sky, beside or above the preview card (`cover`),
 * whichever leaves more room; everything when nothing is selected. */
export function aim(
  galaxies: Galaxy[],
  selected: string | null,
  w: number,
  h: number,
  cover: Box | null = null,
): Camera {
  const g = galaxies.find((x) => x.id === selected);
  if (!g) return fitAll(galaxies, w, h);
  const rooms = cover
    ? [
        { x0: cover.x + cover.w, y0: TOP, x1: w, y1: h },
        { x0: 0, y0: TOP, x1: w, y1: cover.y },
      ]
    : [{ x0: 0, y0: TOP, x1: w, y1: h }];
  const span = 2 * RING * g.r;
  const fit = rooms.map((b) => ({
    b,
    z: Math.min((b.x1 - b.x0 - 2 * PAD) / span, (b.y1 - b.y0 - NAME - 2 * PAD) / span),
  }));
  const { b, z: best } = fit.reduce((p, q) => (q.z > p.z ? q : p));
  const z = Math.max(0.3, Math.min(2.1, best));
  const sx = (b.x0 + b.x1) / 2;
  const sy = (b.y0 + b.y1 - NAME) / 2;
  return { x: g.x - (sx - w / 2) / z, y: g.y - (sy - h / 2) / z, z };
}

/** The galaxy under a screen point, if any. */
export function pick(s: Pick<Scene, 'galaxies' | 'cam' | 'w' | 'h'>, px: number, py: number): Galaxy | null {
  const p = toWorld(s.cam, s.w, s.h, px, py);
  let best: Galaxy | null = null;
  let bd = Infinity;
  for (const g of s.galaxies) {
    const d = Math.hypot(p.x - g.x, p.y - g.y);
    if (d < g.r * RING && d < bd) {
      best = g;
      bd = d;
    }
  }
  return best;
}

function drawGalaxy(
  c: CanvasRenderingContext2D,
  g: Galaxy,
  field: Field | undefined,
  cx: number,
  cy: number,
  z: number,
  now: number,
  dim: boolean,
  reduce: boolean,
) {
  const R = g.r * z;
  const k = dim ? 0.28 : 1;
  const glow = c.createRadialGradient(cx, cy, 0, cx, cy, R * 0.8);
  glow.addColorStop(0, color(g, 80, 0.22 * k));
  glow.addColorStop(0.5, color(g, 60, 0.07 * k));
  glow.addColorStop(1, color(g, 50, 0));
  c.fillStyle = glow;
  c.beginPath();
  c.arc(cx, cy, R * 0.8, 0, 7);
  c.fill();
  if (!field) return;
  // A slow drift, so the galaxy reads as alive; none under reduced motion.
  const rot = reduce ? 0 : now * 0.00002;
  const cos = Math.cos(rot);
  const sin = Math.sin(rot);
  const at = (i: number) => {
    const s = field.stars[i]!;
    return { x: cx + (s.x * cos - s.y * sin) * R, y: cy + (s.x * sin + s.y * cos) * R };
  };
  // Citations: faint threads, then the main routes on top.
  c.strokeStyle = color(g, 70, 0.22 * k);
  c.lineWidth = 0.8;
  c.beginPath();
  for (const e of field.edges) {
    if (e.main) continue;
    const p = at(e.a);
    const q = at(e.b);
    c.moveTo(p.x, p.y);
    c.lineTo(q.x, q.y);
  }
  c.stroke();
  // Main routes widen with the papers that flow through them.
  c.lineCap = 'round';
  for (const e of field.edges) {
    if (!e.main) continue;
    const p = at(e.a);
    const q = at(e.b);
    c.strokeStyle = color(g, 82, 0.7 * k);
    c.lineWidth = (0.8 + Math.log2(e.flow) * 0.55) * Math.sqrt(z);
    c.beginPath();
    c.moveTo(p.x, p.y);
    c.lineTo(q.x, q.y);
    c.stroke();
  }
  const zs = Math.sqrt(z);
  for (let i = 0; i < field.stars.length; i++) {
    const s = field.stars[i]!;
    const { x, y } = at(i);
    const tw = reduce ? 1 : 0.75 + 0.25 * Math.sin(now * 0.002 + s.tw);
    const al = (s.included ? 0.9 : 0.35) * tw * k;
    c.fillStyle = s.included ? color(g, 78, al) : `hsla(${g.hue} 25% 70% / ${al})`;
    const size = (1.4 + s.size * 3.2 + Math.min(4, s.degree) * 0.6) * zs;
    c.beginPath();
    c.arc(x, y, size / 2, 0, 7);
    c.fill();
    // The well-cited and the much-linked get a halo.
    if (s.included && (s.size > 0.45 || s.degree > 2)) {
      c.fillStyle = color(g, 75, 0.12 * tw * k);
      c.beginPath();
      c.arc(x, y, size * 1.8, 0, 7);
      c.fill();
    }
  }
}

export function draw(c: CanvasRenderingContext2D, s: Scene, now: number) {
  const { cam, w, h, reduce } = s;
  c.clearRect(0, 0, w, h);
  for (const d of DUST) {
    const x = (d.x - cam.x * d.d) * Math.sqrt(cam.z) + w / 2;
    const y = (d.y - cam.y * d.d) * Math.sqrt(cam.z) + h / 2;
    if (x < -2 || y < -2 || x > w + 2 || y > h + 2) continue;
    const a = d.a * (reduce ? 1 : 0.6 + 0.4 * Math.sin(now * 0.0012 + d.tw));
    c.fillStyle = `rgba(200,210,235,${a})`;
    c.fillRect(x, y, d.s, d.s);
  }
  const focus = s.hover ?? s.selected;
  c.globalCompositeOperation = 'lighter';
  // Only what is on screen (with room for the name below).
  const seen = s.galaxies.filter((g) => {
    const p = toScreen(cam, w, h, g.x, g.y);
    const R = g.r * cam.z * RING;
    return p.x + R > 0 && p.x - R < w && p.y + R + NAME > 0 && p.y - R < h;
  });
  const mapped = focusFrame(s);
  for (const g of seen) {
    if (g === mapped?.g) continue;
    const p = toScreen(cam, w, h, g.x, g.y);
    drawGalaxy(c, g, s.fields.get(g.id), p.x, p.y, cam.z, now, !!focus && focus !== g, reduce);
    if (g.busy && !reduce) {
      // A sweep rolls outward while the run gathers and screens papers.
      const R = g.r * cam.z;
      const t = (now * 0.0006) % 1;
      c.strokeStyle = color(g, 80, 0.35 * (1 - t));
      c.lineWidth = 1.5;
      c.beginPath();
      c.ellipse(p.x, p.y, R * t * 1.1, R * t * 0.9, 0, 0, 7);
      c.stroke();
    }
  }
  c.globalCompositeOperation = 'source-over';
  if (mapped) drawFocus(c, s, mapped);
  for (const g of seen) {
    const { x: cx, y: cy } = toScreen(cam, w, h, g.x, g.y);
    const R = g.r * cam.z;
    const faded = !!focus && focus !== g;
    // A galaxy drawn as its map says what it needs in the card beside it.
    const isMapped = g === mapped?.g;
    if (s.flagged.has(g.id) && !isMapped) {
      const p = reduce ? 0.5 : (now * 0.0012) % 1;
      c.strokeStyle = `rgba(241,194,125,${(faded ? 0.25 : 0.8) * (1 - p)})`;
      c.lineWidth = 1.2;
      c.beginPath();
      c.arc(cx + R * 0.86, cy - R * 0.72, 4 + p * 8, 0, 7);
      c.stroke();
      c.fillStyle = faded ? 'rgba(241,194,125,.35)' : '#f1c27d';
      c.beginPath();
      c.arc(cx + R * 0.86, cy - R * 0.72, 3.5, 0, 7);
      c.fill();
    }
    if (g === s.selected && !isMapped) {
      c.strokeStyle = 'rgba(169,193,255,.7)';
      c.lineWidth = 1.2;
      c.beginPath();
      c.arc(cx, cy, R * RING, 0, 7);
      c.stroke();
    }
    c.textAlign = 'center';
    c.textBaseline = 'top';
    c.font = `${g === focus ? 600 : 500} 12.5px "IBM Plex Sans", "Noto Sans SC", system-ui, sans-serif`;
    c.fillStyle = faded ? 'rgba(138,147,170,.45)' : g === focus ? '#eef1f8' : 'rgba(214,220,234,.88)';
    if (cam.z < 0.6 && g !== focus) {
      if (R > 14) c.fillText(g.name.length > 16 ? `${g.name.slice(0, 15)}…` : g.name, cx, cy + R * 0.95 + 6);
      continue;
    }
    // Under the ring when it is drawn, so the two never cross.
    const top = cy + R * (g === s.selected ? RING : 0.95);
    c.fillText(g.name, cx, top + 8);
    c.font = '11px "IBM Plex Mono", ui-monospace, monospace';
    c.fillStyle = faded ? 'rgba(91,99,120,.4)' : 'rgba(138,147,170,.85)';
    c.fillText(g.busy ? s.busyLabel(g) : s.detail(g), cx, top + 25);
  }
}

/** "#rrggbb" at an alpha. */
export const rgba = (hex: string, a: number) =>
  `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;

const SANS = '"IBM Plex Sans", "Noto Sans SC", system-ui, sans-serif';
const MONO = '"IBM Plex Mono", ui-monospace, monospace';

interface Box2 {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
const hits = (a: Box2, b: Box2) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/** The papers that get a label: the highest ranked first, as many as fit
 * without overlapping each other or the sector names, more as the map grows. */
export function labelled(
  map: FocusMap,
  R: number,
  width: (text: string) => number,
  taken: Box2[] = [],
  hover: number | null = null,
): { i: number; text: string; box: Box2; left: boolean }[] {
  // A small map (a phone) has no room for titles: only the hovered one.
  const most = R < 120 ? 0 : Math.max(4, Math.min(48, Math.round(R / 26)));
  const order = map.byRank.map((i) => ({ st: map.stars[i]!, i }));
  if (hover !== null && map.stars[hover]) order.unshift({ st: map.stars[hover], i: hover });
  const out: { i: number; text: string; box: Box2; left: boolean }[] = [];
  const boxes = [...taken];
  const seen = new Set<number>();
  for (const { st, i } of order) {
    if (out.length >= most && i !== hover) break;
    // Past a few hundred tries the rest would not fit either.
    if (seen.size > most * 8) break;
    if (seen.has(i)) continue;
    seen.add(i);
    const text = starLabel(st, R > 420 ? 44 : 30);
    const tw = width(text);
    const x = st.x * R;
    const y = st.y * R;
    // Away from the core, so a label runs outward into the sector.
    const left = st.x < 0;
    const box = left
      ? { x0: x - 7 - tw, y0: y - 7, x1: x - 5, y1: y + 7 }
      : { x0: x + 5, y0: y - 7, x1: x + 7 + tw, y1: y + 7 };
    if (i !== hover && boxes.some((b) => hits(b, box))) continue;
    boxes.push(box);
    out.push({ i, text, box, left });
  }
  return out;
}

const labels: { map: FocusMap | null; key: string; out: ReturnType<typeof labelled> } = {
  map: null,
  key: '',
  out: [],
};

/** Lay the labels out again (the web font arrived, so every width changed). */
export function forgetLabels() {
  labels.map = null;
}

/** A sector's name: its community's label, else its top keyword; "other" for the rest. */
export const sectorName = (sec: FocusMap['sectors'][number], other = 'Other') =>
  sec.community === null ? other : (sec.label ?? sec.keywords[0] ?? other);

/** The selected galaxy as its map: sectors, growth rings, main paths, stars, labels. */
function drawFocus(
  c: CanvasRenderingContext2D,
  s: Scene,
  { g, map, cx, cy, R }: NonNullable<ReturnType<typeof focusFrame>>,
) {
  // An index into this map only: a stale one from another map lights nothing.
  const hover = s.hoverStar != null && map.stars[s.hoverStar] ? s.hoverStar : null;
  const k = Math.sqrt(R / 300);
  // A dark disc under the map, so the dust does not read as papers.
  const bg = c.createRadialGradient(cx, cy, 0, cx, cy, R * 1.02);
  bg.addColorStop(0, color(g, 30, 0.35));
  bg.addColorStop(1, 'rgba(8,10,20,0.85)');
  c.fillStyle = bg;
  c.beginPath();
  c.arc(cx, cy, R * 1.02, 0, 7);
  c.fill();
  // Each community's sector, tinted.
  for (const sec of map.sectors) {
    c.fillStyle = rgba(sec.colour, 0.07);
    c.beginPath();
    c.moveTo(cx, cy);
    c.arc(cx, cy, R, sec.a0, sec.a1);
    c.closePath();
    c.fill();
  }
  // Growth rings, open at the top where their years are printed.
  const gap = 0.16;
  c.setLineDash([2, 4]);
  c.strokeStyle = 'rgba(169,180,210,0.22)';
  c.lineWidth = 1;
  for (const ring of map.rings) {
    c.beginPath();
    c.arc(cx, cy, ring.r * R, map.axis + gap, map.axis + 2 * Math.PI - gap);
    c.stroke();
  }
  c.setLineDash([]);

  // Main paths: from the cited paper out to the citing one, wider where more flows.
  const at = (i: number) => ({ x: cx + map.stars[i]!.x * R, y: cy + map.stars[i]!.y * R });
  const hexOf = (i: number) => map.sectors[map.stars[i]!.sector]?.colour ?? '#9a9a9a';
  c.lineCap = 'round';
  for (const l of map.links) {
    const lit = hover === null || l.from === hover || l.to === hover;
    const p = at(l.from);
    const q = at(l.to);
    const hex = hexOf(l.from);
    c.strokeStyle = rgba(hex, lit ? 0.3 + 0.5 * l.weight : 0.08);
    c.lineWidth = (0.7 + 2.6 * l.weight) * k;
    // A slight bend, so paths that share a star part.
    const mx = (p.x + q.x) / 2;
    const my = (p.y + q.y) / 2;
    const bx = mx - (q.y - p.y) * 0.12;
    const by = my + (q.x - p.x) * 0.12;
    c.beginPath();
    c.moveTo(p.x, p.y);
    c.quadraticCurveTo(bx, by, q.x, q.y);
    c.stroke();
    // An arrowhead at the citing paper.
    const ang = Math.atan2(q.y - by, q.x - bx);
    const head = 3 + 3 * l.weight * k;
    c.fillStyle = c.strokeStyle;
    c.beginPath();
    c.moveTo(q.x - Math.cos(ang) * 3, q.y - Math.sin(ang) * 3);
    c.lineTo(q.x - Math.cos(ang - 0.45) * (head + 3), q.y - Math.sin(ang - 0.45) * (head + 3));
    c.lineTo(q.x - Math.cos(ang + 0.45) * (head + 3), q.y - Math.sin(ang + 0.45) * (head + 3));
    c.closePath();
    c.fill();
  }

  // Stars, the heaviest last so they sit on top.
  for (let n = map.byRank.length - 1; n >= 0; n--) {
    const i = map.byRank[n]!;
    const st = map.stars[i]!;
    const { x, y } = at(i);
    const hex = hexOf(i);
    const r = (1.5 + 5 * st.weight) * k;
    if (st.rank < 12 || i === hover) {
      c.fillStyle = rgba(hex, 0.18);
      c.beginPath();
      c.arc(x, y, r * 2.4, 0, 7);
      c.fill();
    }
    c.fillStyle = rgba(hex, hover === null || i === hover ? 0.95 : 0.6);
    c.strokeStyle = 'rgba(8,10,20,0.9)';
    c.lineWidth = 1;
    c.beginPath();
    c.arc(x, y, r, 0, 7);
    c.fill();
    c.stroke();
    if (i === hover) {
      c.strokeStyle = '#eef1f8';
      c.beginPath();
      c.arc(x, y, r + 3, 0, 7);
      c.stroke();
    }
  }

  // The years, on the open axis at the top, over the papers; not in the core,
  // where the axis is narrower than a year.
  c.textAlign = 'center';
  c.textBaseline = 'middle';
  c.font = `10.5px ${MONO}`;
  let lastY = Infinity;
  for (const ring of map.rings) {
    const y = cy - ring.r * R;
    if (lastY - y < 13 || ring.r * R * 2 * gap < 24) continue;
    lastY = y;
    c.lineWidth = 3;
    c.strokeStyle = 'rgba(8,10,20,0.9)';
    c.strokeText(String(ring.year), cx, y);
    c.fillStyle = 'rgba(169,180,210,0.85)';
    c.fillText(String(ring.year), cx, y);
  }

  // Sector names just outside the papers.
  const taken: Box2[] = [];
  // How far names were pushed in from the edges: the labels avoid where they are.
  let clamped = 0;
  for (const sec of map.sectors) {
    const mid = (sec.a0 + sec.a1) / 2;
    let x = cx + Math.cos(mid) * R * 1.05;
    const y = cy + Math.sin(mid) * R * 1.05;
    const left = Math.cos(mid) < -0.15;
    const centre = Math.abs(Math.cos(mid)) <= 0.15;
    c.textAlign = centre ? 'center' : left ? 'right' : 'left';
    c.textBaseline = Math.sin(mid) < 0 ? 'bottom' : 'top';
    const name = sectorName(sec, s.otherLabel);
    const more = s.papersLabel ? s.papersLabel(sec.papers) : String(sec.papers);
    const dy = Math.sin(mid) < 0 ? -14 : 0;
    c.font = `600 12.5px ${SANS}`;
    const tw = Math.max(c.measureText(name).width, c.measureText(more).width);
    // Kept inside the canvas: on a phone a side sector's name would run off it.
    const start = centre ? x - tw / 2 : left ? x - tw : x;
    // The zoom buttons hold the top right corner (Universe.tsx .g-zoom).
    const right = s.w - (y + dy < ZOOM_BOTTOM ? 56 : 4);
    const x0 = Math.min(Math.max(start, 4), right - tw);
    x += x0 - start;
    clamped += Math.abs(x0 - start);
    // Two lines: the name and the count under it, from the baseline up or down.
    const up = dy < 0;
    const box = {
      x0: x0 - cx,
      y0: y + dy - (up ? 13 : 0) - cy,
      x1: x0 + tw - cx,
      y1: y + dy + (up ? 15 : 28) - cy,
    };
    // A narrow sector next to another may have no room for its name: the list says it.
    if (taken.some((b) => hits(b, box))) continue;
    taken.push(box);
    c.fillStyle = sec.colour;
    c.fillText(name, x, y + dy);
    c.font = `11px ${SANS}`;
    c.fillStyle = 'rgba(169,180,210,0.85)';
    c.fillText(more, x, y + dy + 15);
  }

  // Paper labels.
  c.font = `11px ${SANS}`;
  c.textBaseline = 'middle';
  // The labels change with the map, its size and the hover, not every frame.
  // The sector names' boxes are in it, so their language is too.
  const key = `${Math.round(R)}:${hover}:${s.otherLabel}:${s.papersLabel?.(1)}:${Math.round(clamped)}`;
  if (labels.map !== map || labels.key !== key)
    Object.assign(labels, { map, key, out: labelled(map, R, (t) => c.measureText(t).width, taken, hover) });
  for (const lab of labels.out) {
    const st = map.stars[lab.i]!;
    c.textAlign = lab.left ? 'right' : 'left';
    const x = cx + st.x * R + (lab.left ? -6 : 6);
    const y = cy + st.y * R;
    c.lineWidth = 3;
    c.strokeStyle = 'rgba(8,10,20,0.85)';
    c.strokeText(lab.text, x, y);
    c.fillStyle = lab.i === hover ? '#ffffff' : 'rgba(226,231,242,0.92)';
    c.fillText(lab.text, x, y);
  }
}

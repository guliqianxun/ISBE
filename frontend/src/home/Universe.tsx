// The universe canvas: drag to pan, wheel, pinch or the buttons to zoom,
// hover for a galaxy's name, click to select it (the camera glides over).
// Arrow keys step through the galaxies and Escape lets go; a list of the
// galaxies sits under the canvas for keyboards and screen readers, and shows
// when it has focus. The canvas only redraws while something moves: the
// camera, a hover, or the galaxies themselves (slowly, unless reduced motion).
import { useEffect, useId, useRef, useState } from 'react';
import { useT } from '../i18n';
import { textLang } from '../question/lang';
import type { Field } from './field';
import type { FocusMap, FocusStar } from './focus';
import type { Galaxy } from './galaxies';
import {
  aim,
  draw,
  fitAll,
  focusFrame,
  forgetLabels,
  pick,
  pickStar,
  sectorName,
  toScreen,
  toWorld,
  type Box,
  type Camera,
  type Scene,
} from './sky';

export interface UniverseProps {
  galaxies: Galaxy[];
  flagged: Set<string>;
  fields: Map<string, Field>;
  selected: string | null;
  /** The selected galaxy's map from its graph (focus.ts): drawn once it is loaded. */
  focus?: FocusMap | null;
  /** Where the preview card covers the canvas, so a selection is framed beside it. */
  cover?: Box | null;
  onSelect: (id: string | null) => void;
  onOpen: (id: string) => void;
  /** The galaxies large enough on screen to show their stars, largest first. */
  onView?: (ids: string[]) => void;
}

/** A galaxy shows its stars from this many pixels across. */
export const DETAIL_PX = 40;
/** The paper list under a galaxy's map names this many papers of each sector. */
export const PAPERS_PER_SECTOR = 8;
/** Frames per second while only the stars twinkle. */
const IDLE_FPS = 20;

const reducedMotion = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

const clampZ = (z: number) => Math.max(0.3, Math.min(3.5, z));

interface View {
  cam: Camera;
  goal: Camera;
  w: number;
  h: number;
  dirty: boolean;
}

/** Zoom by `f` keeping the world point under (px, py) where it is. */
function zoomAt(v: View, px: number, py: number, f: number) {
  const b = toWorld(v.cam, v.w, v.h, px, py);
  v.goal.z = clampZ(v.goal.z * f);
  v.cam.z = v.goal.z;
  v.goal.x = b.x - (px - v.w / 2) / v.cam.z;
  v.goal.y = b.y - (py - v.h / 2) / v.cam.z;
  Object.assign(v.cam, { x: v.goal.x, y: v.goal.y });
  v.dirty = true;
}

/** Step through the galaxies from the selection; from nothing, the first or the last. */
export function stepFrom(ids: string[], selected: string | null, d: 1 | -1): string | undefined {
  if (!ids.length) return undefined;
  const i = ids.indexOf(selected ?? '');
  if (i < 0) return d > 0 ? ids[0] : ids.at(-1);
  return ids[(i + d + ids.length) % ids.length];
}

/** The galaxies drawn large enough to show their stars, largest first. */
export function inView(galaxies: Galaxy[], cam: Camera, w: number, h: number): string[] {
  return galaxies
    .map((g) => ({ g, p: toScreen(cam, w, h, g.x, g.y), R: g.r * cam.z }))
    .filter(({ p, R }) => 2 * R >= DETAIL_PX && p.x + R > 0 && p.x - R < w && p.y + R > 0 && p.y - R < h)
    .sort((a, b) => b.R - a.R)
    .map(({ g }) => g.id);
}

export function Universe({
  galaxies,
  flagged,
  fields,
  selected,
  focus = null,
  cover = null,
  onSelect,
  onOpen,
  onView,
}: UniverseProps) {
  const t = useT();
  const uid = useId();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [tip, setTip] = useState<{ g: Galaxy; x: number; y: number } | null>(null);
  const [starTip, setStarTip] = useState<{ s: FocusStar; label: string; x: number; y: number } | null>(null);
  /** Whether the selected galaxy is drawn as its map right now (it is not while small). */
  const [mapped, setMapped] = useState(false);
  // The drawing loop reads the latest props from here, so it starts once.
  const live = useRef({ galaxies, flagged, fields, selected, focus, cover, t, onView });
  const view = useRef({
    cam: { x: 0, y: 0, z: 1 } as Camera,
    goal: { x: 0, y: 0, z: 1 } as Camera,
    w: 0,
    h: 0,
    hover: null as Galaxy | null,
    hoverStar: null as number | null,
    fitted: false,
    /** Something changed that the next frame must show. */
    dirty: true,
  });
  useEffect(() => {
    live.current = { galaxies, flagged, fields, selected, focus, cover, t, onView };
    view.current.dirty = true;
  });
  // Text widths change when the web font arrives: the map's labels are laid out again.
  useEffect(() => {
    let live = true;
    void document.fonts?.ready.then(() => {
      if (!live) return;
      forgetLabels();
      view.current.dirty = true;
    });
    return () => {
      live = false;
    };
  }, []);
  // A lit paper is an index into one map: another selection, or the same
  // galaxy's map read again, lets it go.
  const [tipFor, setTipFor] = useState({ selected, focus });
  if (tipFor.selected !== selected || tipFor.focus !== focus) {
    setTipFor({ selected, focus });
    setStarTip(null);
  }
  useEffect(() => {
    view.current.hoverStar = null;
    view.current.dirty = true;
  }, [selected, focus]);

  // Glide to the selection, or back out to everything, when that changes:
  // not on every poll, which would undo the person's own zoom and pan.
  const target = galaxies.some((g) => g.id === selected)
    ? `${selected}|${cover ? `${cover.x}:${cover.y}:${cover.w}:${cover.h}` : ''}`
    : galaxies.map((g) => g.id).join(' ');
  useEffect(() => {
    const v = view.current;
    const { galaxies: gs, selected: sel, cover: cv } = live.current;
    if (v.w) Object.assign(v.goal, aim(gs, sel, v.w, v.h, cv));
  }, [target]);

  useEffect(() => {
    const el = canvas.current;
    const c = el?.getContext('2d');
    if (!el || !c) return;
    const v = view.current;
    const reduce = reducedMotion();
    let dpr = 1;
    const resize = () => {
      const r = el.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      v.w = r.width;
      v.h = r.height;
      el.width = v.w * dpr;
      el.height = v.h * dpr;
      const { galaxies: gs, selected: sel, cover: cv } = live.current;
      Object.assign(v.goal, aim(gs, sel, v.w, v.h, cv));
      // The first look starts from everything, then glides to a selection.
      if (!v.fitted) Object.assign(v.cam, fitAll(gs, v.w, v.h));
      v.fitted = gs.length > 0;
      v.dirty = true;
    };
    resize();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
    ro?.observe(el);
    // Nothing to draw while the canvas is scrolled out of sight.
    let shown = true;
    const io =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver(([e]) => {
            shown = !!e?.isIntersecting;
            v.dirty = true;
          });
    io?.observe(el);
    let raf = 0;
    let last = 0;
    let reported = '';
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const {
        galaxies: gs,
        flagged: fl,
        fields: fs,
        selected: sel,
        focus: fm,
        t: tr,
        onView: report,
      } = live.current;
      if (!v.fitted && gs.length) resize();
      const k = reduce ? 1 : 0.12;
      const moving =
        Math.abs(v.goal.x - v.cam.x) * v.cam.z > 0.2 ||
        Math.abs(v.goal.y - v.cam.y) * v.cam.z > 0.2 ||
        Math.abs(v.goal.z - v.cam.z) > 0.001;
      if (moving) {
        v.cam.x += (v.goal.x - v.cam.x) * k;
        v.cam.y += (v.goal.y - v.cam.y) * k;
        v.cam.z += (v.goal.z - v.cam.z) * k;
      } else if (report) {
        // Once the camera rests, say which galaxies are worth their stars.
        const ids = inView(gs, v.cam, v.w, v.h);
        const key = ids.join(' ');
        if (key !== reported) {
          reported = key;
          report(ids);
        }
      }
      if (!shown) return;
      // Still: draw on change only. Twinkling: a few frames a second is enough.
      const due = moving || v.dirty || (!reduce && now - last >= 1000 / IDLE_FPS);
      if (!due) return;
      last = now;
      v.dirty = false;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      const scene: Scene = {
        galaxies: gs,
        cam: v.cam,
        w: v.w,
        h: v.h,
        hover: v.hover,
        selected: gs.find((g) => g.id === sel) ?? null,
        flagged: fl,
        fields: fs,
        reduce,
        busyLabel: (g) =>
          g.run.status === 'queued'
            ? tr('gQueued')
            : g.run.stage === 'screen'
              ? tr('gScreening', { n: g.papers })
              : tr('gGathering', { n: g.papers }),
        detail: (g) => tr('gDetail', { n: g.papers, i: g.included }),
        focus: fm,
        hoverStar: v.hoverStar,
        papersLabel: (n) => tr('gSectorPapers', { n }),
        otherLabel: tr('gOther'),
      };
      draw(c, scene, now);
      setMapped(!!focusFrame(scene));
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      io?.disconnect();
    };
  }, []);

  useEffect(() => {
    // React's onWheel is passive; zooming must stop the page from scrolling.
    const el = canvas.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(view.current, e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
      // The map moves under a paper's tip: it goes.
      setStarTip(null);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);

  const zoom = (f: number) => {
    const v = view.current;
    setStarTip(null);
    v.goal.z = clampZ(v.goal.z * f);
  };

  // Pointers: one drags, two pinch, a press and release without moving selects.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);
  const pinch = useRef<{ d: number } | null>(null);
  const local = (x: number, y: number) => {
    const r = canvas.current!.getBoundingClientRect();
    return { px: x - r.left, py: y - r.top };
  };
  const scene = () => ({
    galaxies,
    ...view.current,
    selected: galaxies.find((g) => g.id === selected) ?? null,
    focus,
  });
  /** Light a paper of the map and show its tip: from the pointer, a tap or the paper list. */
  const showStar = (i: number | null, at?: { px: number; py: number }) => {
    const v = view.current;
    if (i !== v.hoverStar) v.dirty = true;
    v.hoverStar = i;
    const f = focusFrame(scene());
    const s = i === null || !f ? null : f.map.stars[i]!;
    if (!s || !f) {
      setStarTip(null);
      return;
    }
    const sector = f.map.sectors[s.sector];
    const p = at ?? { px: f.cx + s.x * f.R, py: f.cy + s.y * f.R };
    // Kept inside the canvas: the tip is centred on its point and up to 340 px wide.
    const x = Math.min(Math.max(p.px, Math.min(178, v.w / 2)), Math.max(v.w / 2, v.w - 178));
    setStarTip({ s, label: sector ? sectorName(sector, t('gOther')) : '', x, y: p.py });
  };
  const startDrag = (x: number, y: number, moved: boolean) => {
    const v = view.current;
    drag.current = { x, y, cx: v.goal.x, cy: v.goal.y, moved };
  };
  const release = (id: number) => {
    pointers.current.delete(id);
    if (pinch.current && pointers.current.size < 2) {
      pinch.current = null;
      // The finger left behind keeps panning, but never clicks.
      const [rest] = pointers.current.values();
      if (rest) startDrag(rest.x, rest.y, true);
      else drag.current = null;
    }
  };

  const step = (d: 1 | -1) => {
    const id = stepFrom(
      galaxies.map((g) => g.id),
      selected,
      d,
    );
    if (id) onSelect(id);
  };

  const current = galaxies.find((g) => g.id === selected);

  return (
    <div className="g-universe">
      <canvas
        ref={canvas}
        tabIndex={0}
        aria-label={t('gUniverse')}
        data-over={tip ? '' : undefined}
        onPointerDown={(e) => {
          pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
          e.currentTarget.setPointerCapture?.(e.pointerId);
          if (pointers.current.size === 2) {
            const [a, b] = [...pointers.current.values()];
            pinch.current = { d: Math.hypot(a!.x - b!.x, a!.y - b!.y) };
            drag.current = null;
            setTip(null);
            setStarTip(null);
          } else if (pointers.current.size === 1) startDrag(e.clientX, e.clientY, false);
        }}
        onPointerMove={(e) => {
          const v = view.current;
          if (pointers.current.has(e.pointerId))
            pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
          if (pinch.current) {
            const [a, b] = [...pointers.current.values()];
            const d = Math.hypot(a!.x - b!.x, a!.y - b!.y);
            if (d > 0 && pinch.current.d > 0) {
              const { px, py } = local((a!.x + b!.x) / 2, (a!.y + b!.y) / 2);
              zoomAt(v, px, py, d / pinch.current.d);
              setStarTip(null);
            }
            pinch.current.d = d;
            return;
          }
          const d = drag.current;
          if (d) {
            const dx = e.clientX - d.x;
            const dy = e.clientY - d.y;
            if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
            if (d.moved) {
              v.goal.x = d.cx - dx / v.cam.z;
              v.goal.y = d.cy - dy / v.cam.z;
              Object.assign(v.cam, { x: v.goal.x, y: v.goal.y });
              v.dirty = true;
              setTip(null);
              setStarTip(null);
            }
            return;
          }
          if (e.pointerType === 'touch') return;
          const { px, py } = local(e.clientX, e.clientY);
          const sc = scene();
          // Over the selected galaxy's map, the papers answer the pointer.
          const star = pickStar(sc, px, py);
          showStar(star, { px, py });
          const g = focusFrame(sc) && star !== null ? null : pick(sc, px, py);
          if (g !== v.hover) v.dirty = true;
          v.hover = g;
          setTip(g && g.id !== selected ? { g, x: px, y: py } : null);
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === 'touch') return;
          view.current.hover = null;
          view.current.hoverStar = null;
          view.current.dirty = true;
          setTip(null);
          setStarTip(null);
        }}
        onPointerCancel={(e) => {
          // The browser took the gesture over (a scroll, a system swipe): forget it.
          release(e.pointerId);
          if (!pinch.current) drag.current = null;
        }}
        onPointerUp={(e) => {
          const wasPinch = !!pinch.current;
          const d = pointers.current.has(e.pointerId) ? drag.current : null;
          release(e.pointerId);
          if (wasPinch || !d) return;
          drag.current = null;
          if (d.moved) return;
          const { px, py } = local(e.clientX, e.clientY);
          // A tap on a paper of the map shows the paper; the galaxy stays selected.
          const star = pickStar(scene(), px, py);
          if (star !== null) {
            showStar(star);
            return;
          }
          showStar(null);
          const g = pick(scene(), px, py);
          onSelect(g ? g.id : null);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onSelect(null);
          else if (e.key === 'ArrowRight') step(1);
          else if (e.key === 'ArrowLeft') step(-1);
          else if (e.key === 'Enter' && selected) onOpen(selected);
          else return;
          e.preventDefault();
        }}
      />
      {tip && (
        <div className="g-tip" style={{ left: tip.x, top: tip.y }} aria-hidden="true">
          <b>{tip.g.name}</b> <span>· {t('gDetail', { n: tip.g.papers, i: tip.g.included })}</span>
        </div>
      )}
      {starTip && (
        <div className="g-tip g-star-tip" style={{ left: starTip.x, top: starTip.y }} aria-hidden="true">
          <b lang={textLang(starTip.s.title)}>{starTip.s.title}</b>
          <span>
            {[starTip.s.year ?? '', starTip.label, t('gCited', { n: starTip.s.cited })]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
      )}
      {mapped && focus && <p className="g-key">{t('gFocusKey')}</p>}
      <div className="g-zoom">
        <button type="button" aria-label={t('gZoomIn')} onClick={() => zoom(1.3)}>
          +
        </button>
        <button type="button" aria-label={t('gZoomOut')} onClick={() => zoom(1 / 1.3)}>
          −
        </button>
        <button type="button" aria-label={t('gZoomFit')} onClick={() => onSelect(null)}>
          ⤢
        </button>
      </div>
      <ul className="g-list" aria-label={t('gList')}>
        {galaxies.map((g) => (
          <li key={g.id}>
            <button type="button" aria-pressed={g.id === selected} onClick={() => onSelect(g.id)}>
              {g.run.question}
            </button>
          </li>
        ))}
      </ul>
      {mapped && focus && (
        // The map's papers for keyboards and screen readers: each sector's
        // best-ranked, as the map labels them; focusing one lights it.
        <ul className="g-list g-papers" aria-label={t('gMapList')}>
          {focus.sectors.map((sec, n) => (
            <li key={n}>
              <span id={`${uid}-s${n}`}>
                {sectorName(sec, t('gOther'))} · {t('gSectorPapers', { n: sec.papers })}
              </span>
              <ul aria-labelledby={`${uid}-s${n}`}>
                {focus.byRank
                  .filter((i) => focus.stars[i]!.sector === n)
                  .slice(0, PAPERS_PER_SECTOR)
                  .map((i) => {
                    const s = focus.stars[i]!;
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          onFocus={() => showStar(i)}
                          onBlur={() => showStar(null)}
                          onClick={() => showStar(i)}
                        >
                          <span lang={textLang(s.title)}>{s.title}</span>
                          {s.year !== null && ` · ${s.year}`}
                          {/* What the tip shows, for those who do not see it. */}
                          <span className="sr-only"> · {t('gCited', { n: s.cited })}</span>
                        </button>
                      </li>
                    );
                  })}
              </ul>
            </li>
          ))}
        </ul>
      )}
      <p className="sr-only" aria-live="polite">
        {current ? t('gSelected', { name: current.run.question }) : ''}
      </p>
    </div>
  );
}

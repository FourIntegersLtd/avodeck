// One or many document pages with one toolbar. Pages come in as `src` (the slice fetches the picture; no
// fetching and no storage links here). Turning, fitting and "clearer" only change how pages are shown.
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { t } from "../i18n";
import { drawnSize, turnBy, usePageView, viewFilter, type Fit, type View } from "./pageView";
import { PageViewToolbar, type PageViewTools } from "./PageViewToolbar";

export interface ViewerPage {
  /** The picture's address (a blob URL the slice made). Null while it loads. */
  src: string | null;
  /** Words for the picture, from a key (for example "Page 3"). */
  label: string;
  /** Drawn over the picture in page fractions; turned with it. */
  children?: ReactNode;
}

export interface PageViewerProps {
  pages: ViewerPage[];
  tools?: PageViewTools;
  /** Start here (the owner may keep its own copy). */
  initialView?: View;
  initialTurn?: number;
  /** Controlled: the viewer shows these and only reports changes. */
  view?: View;
  turn?: number;
  onView?: (view: View) => void;
  onTurn?: (turn: number) => void;
  /** Whether the toolbar shows at all. */
  toolbar?: boolean;
}

const NATURAL = { width: 1000, height: 1400 };

function Paper({ page, turn, fit, zoom, filter, onZoom, onScale, first }: { page: ViewerPage; turn: number; fit: Fit; zoom: number; filter?: string; onZoom: (points: number) => void; onScale: (percent: number) => void; first: boolean }) {
  const [natural, setNatural] = useState(NATURAL);
  const [space, setSpace] = useState({ width: 0, height: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(onZoom);
  zoomRef.current = onZoom;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSpace((s) => (s.width === el.clientWidth && s.height === el.clientHeight ? s : { width: el.clientWidth, height: el.clientHeight }));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  // Ctrl and the wheel (a trackpad pinch arrives the same way) zoom; not passive so the browser does not zoom the screen.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const size = Math.max(2, Math.min(10, Math.abs(e.deltaY) / 10));
      zoomRef.current(e.deltaY < 0 ? size : -size);
    };
    const touches = new Map<number, { x: number; y: number }>();
    let spread = 0;
    const apart = () => {
      const [a, b] = [...touches.values()];
      return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
    };
    const down = (e: PointerEvent) => {
      if (e.pointerType !== "touch") return;
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      spread = apart();
    };
    const move = (e: PointerEvent) => {
      if (e.pointerType !== "touch" || !touches.has(e.pointerId)) return;
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const now = apart();
      if (spread > 0 && now > 0 && Math.abs(now - spread) > 8) {
        zoomRef.current((now - spread) / 4);
        spread = now;
      }
    };
    const up = (e: PointerEvent) => {
      touches.delete(e.pointerId);
      spread = apart();
    };
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, []);

  const quarter = turnBy(turn, 0);
  const sideways = quarter % 180 !== 0;
  const drawn = space.width > 0 ? drawnSize(natural, space, quarter, { fit, zoom }) : null;
  const percent = drawn ? Math.round((drawn.width / space.width) * 100) : null;
  useEffect(() => {
    if (first && percent !== null) onScale(percent);
  }, [first, percent, onScale]);

  const frame: CSSProperties | undefined = drawn ? { width: drawn.width, height: drawn.height } : undefined;
  const style: CSSProperties = drawn
    ? { width: sideways ? drawn.height : drawn.width, height: sideways ? drawn.width : drawn.height, transform: `translate(-50%, -50%) rotate(${quarter}deg)` }
    : { transform: quarter ? `rotate(${quarter}deg)` : undefined };
  return (
    <div className="av-pageview__paper" ref={ref} tabIndex={0} aria-label={page.label}>
      <div className={drawn ? "av-pageview__frame av-pageview__frame--sized" : "av-pageview__frame"} style={frame}>
        <div className={drawn ? "av-pageview__page av-pageview__page--sized" : "av-pageview__page"} data-testid="page-turn" data-turn={quarter} style={style}>
          {page.src ? (
            <img
              className="av-pageview__image"
              src={page.src}
              alt={page.label}
              style={filter ? { filter } : undefined}
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) setNatural({ width: img.naturalWidth, height: img.naturalHeight });
              }}
            />
          ) : (
            <div className="av-pageview__image av-pageview__image--empty" role="img" aria-label={page.label} />
          )}
          {page.src && page.children}
        </div>
      </div>
    </div>
  );
}

export function PageViewer({ pages, tools, initialView, initialTurn, view: viewProp, turn: turnProp, onView, onTurn, toolbar = true }: PageViewerProps) {
  const state = usePageView({ view: viewProp ?? initialView, turn: turnProp ?? initialTurn, onView, onTurn });
  const [scale, setScale] = useState<number | null>(null);
  const view = viewProp ?? state.view;
  const turn = turnProp ?? state.turn;
  const filter = viewFilter(view);
  const now = scale ?? 100;
  // A controlled value is not changed here: the viewer asks the owner for the next one.
  const step = (by: number) => (turnProp === undefined ? state.setTurn(turnBy(turn, by)) : onTurn?.(turnBy(turn, by)));
  const change = (next: Partial<View>) => {
    if (viewProp === undefined) {
      if (next.fit) state.setFit(next.fit);
      state.setClearer(next);
    } else onView?.({ ...viewProp, ...next });
  };
  const zoom = (points: number) => {
    if (viewProp === undefined) state.zoomBy(points, now);
    else onView?.({ ...viewProp, fit: "zoom", zoom: Math.min(400, Math.max(50, Math.round((viewProp.fit === "zoom" ? viewProp.zoom : now) + points))) });
  };
  return (
    <div className="av-pageview">
      {toolbar && (
        <PageViewToolbar
          percent={view.fit === "zoom" ? view.zoom : now}
          tools={tools}
          fit={view.fit}
          brightness={view.brightness}
          contrast={view.contrast}
          onZoomIn={() => zoom(25)}
          onZoomOut={() => zoom(-25)}
          onTurnLeft={() => step(-90)}
          onTurnRight={() => step(90)}
          onFit={(fit) => change({ fit })}
          onBrightness={(brightness) => change({ brightness })}
          onContrast={(contrast) => change({ contrast })}
          onReset={() => {
            if (viewProp === undefined) state.reset();
            else onView?.({ ...viewProp, fit: "width", zoom: 100, brightness: 100, contrast: 100 });
            if (turnProp !== undefined) onTurn?.(0);
          }}
        />
      )}
      <div className="av-pageview__stack">
        {pages.map((p, i) => (
          <Paper key={i} page={p} turn={turn} fit={view.fit} zoom={view.zoom} filter={filter} onZoom={zoom} onScale={setScale} first={i === 0} />
        ))}
      </div>
    </div>
  );
}

/** The words for a page's picture, from the key. */
export const pageViewAlt = (page: number) => t("viewer.page_alt", { page });

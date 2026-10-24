// The page's picture, with anything drawn over it placed in page fractions. Turning, fitting and
// "Make it clearer" only change how it is shown: the picture and everything drawn on it sit in one box
// that is turned as a whole, so highlights and the word picker stay over the same words (rule 2).
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { pageImage, peekPage } from "./pageImageCache";
import { drawnSize, type Fit } from "./viewer";
import { w } from "./words";

interface Props {
  caseId: string;
  pageNo: number;
  /** Clockwise quarter turn for reading (display only). */
  turn?: number;
  view?: { fit: Fit; zoom: number };
  /** A CSS filter for the picture only (brightness and contrast); the highlights keep their colours. */
  filter?: string;
  /** Ctrl and the wheel, or a pinch: zoom by this many points. */
  onZoom?: (points: number) => void;
  /** The page's drawn width as a share of the space's width, in percent, when known. */
  onScale?: (percent: number) => void;
  children?: ReactNode;
}

const NATURAL = { width: 1000, height: 1400 };

export function PageImage({ caseId, pageNo, turn = 0, view = { fit: "width", zoom: 100 }, filter, onZoom, onScale, children }: Props) {
  const [src, setSrc] = useState<string | null>(() => peekPage(caseId, pageNo));
  const [natural, setNatural] = useState(NATURAL);
  const [space, setSpace] = useState({ width: 0, height: 0 });
  const paperRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(onZoom);
  zoomRef.current = onZoom;

  useEffect(() => {
    let live = true;
    setSrc(peekPage(caseId, pageNo));
    pageImage(caseId, pageNo)
      .then((url) => live && setSrc(url))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [caseId, pageNo]);

  // The space the page has, kept up to date as the window or the split changes.
  useEffect(() => {
    const el = paperRef.current;
    if (!el) return;
    const measure = () => setSpace((s) => (s.width === el.clientWidth && s.height === el.clientHeight ? s : { width: el.clientWidth, height: el.clientHeight }));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  // Ctrl and the wheel (and a trackpad pinch, which arrives the same way) zoom; the browser must not zoom
  // the whole screen, so this listener is not passive. Two fingers on a touch screen pinch too; one mouse
  // button held drags the page about.
  useEffect(() => {
    const el = paperRef.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey || !zoomRef.current) return;
      e.preventDefault();
      const size = Math.max(2, Math.min(10, Math.abs(e.deltaY) / 10));
      zoomRef.current(e.deltaY < 0 ? size : -size);
    };
    const touches = new Map<number, { x: number; y: number }>();
    let spread = 0;
    let drag: { x: number; y: number; left: number; top: number } | null = null;
    const apart = () => {
      const [a, b] = [...touches.values()];
      return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
    };
    const down = (e: PointerEvent) => {
      if (e.pointerType === "touch") {
        touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
        spread = apart();
        return;
      }
      if (e.button !== 0 || (e.target as HTMLElement).closest("button, a, input, textarea, select")) return;
      drag = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
      el.classList.add("rd-paper--dragging");
    };
    const move = (e: PointerEvent) => {
      if (e.pointerType === "touch" && touches.has(e.pointerId)) {
        touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const now = apart();
        if (spread > 0 && now > 0 && Math.abs(now - spread) > 8 && zoomRef.current) {
          zoomRef.current((now - spread) / 4);
          spread = now;
        }
        return;
      }
      if (!drag) return;
      el.scrollLeft = drag.left - (e.clientX - drag.x);
      el.scrollTop = drag.top - (e.clientY - drag.y);
    };
    const up = (e: PointerEvent) => {
      touches.delete(e.pointerId);
      spread = apart();
      drag = null;
      el.classList.remove("rd-paper--dragging");
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

  const quarter = ((turn % 360) + 360) % 360;
  const sideways = quarter % 180 !== 0;
  const drawn = space.width > 0 ? drawnSize(natural, space, quarter, view) : null;
  const percent = drawn ? Math.round((drawn.width / space.width) * 100) : null;
  useEffect(() => {
    if (percent !== null) onScale?.(percent);
  }, [percent, onScale]);

  const frame: CSSProperties | undefined = drawn ? { width: drawn.width, height: drawn.height } : undefined;
  const page: CSSProperties = drawn
    ? { width: sideways ? drawn.height : drawn.width, height: sideways ? drawn.width : drawn.height, transform: `translate(-50%, -50%) rotate(${quarter}deg)` }
    : { transform: quarter ? `rotate(${quarter}deg)` : undefined };
  const alt = w("read.page_alt", { page: pageNo });
  return (
    <div className="rd-paper" ref={paperRef} tabIndex={0} aria-label={alt}>
      <div className={drawn ? "rd-frame rd-frame--sized" : "rd-frame"} style={frame}>
        <div className={drawn ? "rd-page rd-page--sized" : "rd-page"} data-testid="page-turn" data-turn={quarter} style={page}>
          {src ? (
            <img
              className="rd-page__image"
              src={src}
              alt={alt}
              style={filter ? { filter } : undefined}
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) setNatural({ width: img.naturalWidth, height: img.naturalHeight });
              }}
            />
          ) : (
            <div className="rd-page__image rd-page__image--empty" role="img" aria-label={alt} />
          )}
          {src && children}
        </div>
      </div>
    </div>
  );
}

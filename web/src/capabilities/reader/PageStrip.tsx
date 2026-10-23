// The page strip beside the page (screen 09): a small picture of every page with its read mark. Only the
// pages in view (and a few either side) are drawn, so 4,000 pages stay quick, and a small picture is asked
// for only once its place is drawn. Keyboard: arrows move, Page Up and Page Down jump, Home and End go to the
// ends, Enter opens the page.
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Listed } from "./bundlePages";
import { smallPageImage } from "./pageImageCache";
import { stripWindow } from "./viewer";
import { w } from "./words";

const ITEM = 136;
const OVERSCAN = 4;
const FALLBACK_VIEW = 800;

interface Props {
  caseId: string;
  pages: Listed[];
  current: number;
  /** Whether the page open now was read (it may have been read after the list arrived). */
  currentRead: boolean;
  onOpen: (pageNo: number) => void;
}

function Small({ caseId, pageNo }: { caseId: string; pageNo: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    // A short wait, so scrolling past hundreds of pages does not ask for each one.
    const wait = setTimeout(() => {
      smallPageImage(caseId, pageNo)
        .then((url) => live && setSrc(url))
        .catch(() => undefined);
    }, 80);
    return () => {
      live = false;
      clearTimeout(wait);
    };
  }, [caseId, pageNo]);
  return src ? <img className="rd-strip__image" src={src} alt="" /> : <span className="rd-strip__image rd-strip__image--empty" />;
}

export function PageStrip({ caseId, pages, current, currentRead, onOpen }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const view = () => ref.current?.clientHeight || FALLBACK_VIEW;
  const at = Math.max(0, pages.findIndex((p) => p.page_no === current));
  const [active, setActive] = useState(at);
  const [scroll, setScroll] = useState(() => Math.max(0, at * ITEM - FALLBACK_VIEW / 2 + ITEM / 2));

  const scrollTo = (top: number) => {
    setScroll(top);
    if (ref.current) ref.current.scrollTop = top;
  };

  // Open with the current page in the middle of the strip.
  useLayoutEffect(() => {
    scrollTo(Math.max(0, at * ITEM - view() / 2 + ITEM / 2));
    // Only when the strip first appears; moving about is the expert's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function move(to: number) {
    const i = Math.min(pages.length - 1, Math.max(0, to));
    setActive(i);
    const h = view();
    if (i * ITEM < scroll) scrollTo(i * ITEM);
    else if ((i + 1) * ITEM > scroll + h) scrollTo((i + 1) * ITEM - h);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const page = Math.max(1, Math.floor(view() / ITEM));
    const keys: Record<string, () => void> = {
      ArrowDown: () => move(active + 1),
      ArrowRight: () => move(active + 1),
      ArrowUp: () => move(active - 1),
      ArrowLeft: () => move(active - 1),
      PageDown: () => move(active + page),
      PageUp: () => move(active - page),
      Home: () => move(0),
      End: () => move(pages.length - 1),
      Enter: () => pages[active] && onOpen(pages[active]!.page_no),
      " ": () => pages[active] && onOpen(pages[active]!.page_no),
    };
    const run = keys[e.key];
    if (!run) return;
    e.preventDefault();
    e.stopPropagation();
    run();
  }

  const { start, end } = stripWindow({ count: pages.length, itemSize: ITEM, scroll, view: view(), overscan: OVERSCAN });
  const shown = pages.slice(start, end);
  const activePage = pages[active];
  return (
    <div
      ref={ref}
      className="rd-strip"
      role="listbox"
      aria-label={w("read.strip")}
      tabIndex={0}
      aria-activedescendant={activePage ? `rd-strip-${activePage.page_no}` : undefined}
      onKeyDown={onKeyDown}
      onScroll={(e) => setScroll(e.currentTarget.scrollTop)}
    >
      <div className="rd-strip__track" style={{ height: pages.length * ITEM }}>
        {shown.map((p, i) => {
          const read = p.page_no === current ? currentRead || p.read : p.read;
          const classes = ["rd-strip__item", p.page_no === current && "rd-strip__item--current", start + i === active && "rd-strip__item--active"].filter(Boolean).join(" ");
          return (
            <div
              key={p.page_no}
              id={`rd-strip-${p.page_no}`}
              role="option"
              aria-selected={p.page_no === current}
              aria-setsize={pages.length}
              aria-posinset={start + i + 1}
              aria-label={w(read ? "read.strip_read" : "read.strip_unread", { page: p.page_no })}
              className={classes}
              style={{ top: (start + i) * ITEM }}
              onClick={() => onOpen(p.page_no)}
            >
              <Small caseId={caseId} pageNo={p.page_no} />
              <span className="rd-strip__label" aria-hidden="true">
                <span className="rd-strip__no">{p.page_no}</span>
                {read && (
                  <svg width="16" height="16" viewBox="0 0 24 24" focusable="false" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12l5 5 9-10" />
                  </svg>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

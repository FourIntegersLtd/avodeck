import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

/** The browser has the Popover API (the menu then sits in the top layer, above the bar and the page). */
const POPOVER = typeof HTMLElement !== "undefined" && "showPopover" in HTMLElement.prototype;

/** Right-aligned under the trigger, up when there is no room below; full width at the bottom on narrow screens. */
export function placeMenu(el: HTMLElement, button: HTMLElement | null) {
  if (!button || window.innerWidth <= 900) {
    el.style.top = el.style.left = el.style.maxHeight = "";
    return;
  }
  const r = button.getBoundingClientRect();
  const gap = 8;
  const below = window.innerHeight - r.bottom - gap * 2;
  const above = r.top - gap * 2;
  const down = below >= Math.min(el.scrollHeight, 480) || below >= above;
  el.style.maxHeight = `${Math.max(160, down ? below : above)}px`;
  const height = Math.min(el.scrollHeight, el.offsetHeight || el.scrollHeight);
  el.style.top = `${down ? r.bottom + gap : Math.max(gap, r.top - gap - height)}px`;
  el.style.left = `${Math.min(Math.max(gap, r.right - el.offsetWidth), window.innerWidth - el.offsetWidth - gap)}px`;
}

const TICK = (
  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12l5 5 9-10" />
  </svg>
);
const CHEVRON = (
  <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export interface MenuProps {
  /** The menu's name for assistive technology. */
  label: string;
  /** What the trigger button shows (an icon and a word). */
  trigger: ReactNode;
  children: ReactNode;
  /** Hide the little chevron on the trigger. */
  noChevron?: boolean;
}

/** A quiet trigger and a popover of sections and rows. Closes on Escape or a click outside; focus goes back to the trigger. */
export function Menu({ label, trigger, children, noChevron }: MenuProps) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!panel.current?.contains(target) && !button.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useLayoutEffect(() => {
    const el = panel.current;
    if (!el) return;
    if (POPOVER) {
      try {
        if (!el.matches(":popover-open")) el.showPopover();
      } catch {
        /* already shown */
      }
    }
    placeMenu(el, button.current);
    el.querySelector<HTMLElement>("button:not(:disabled), a")?.focus();
  }, [open]);

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), a"));
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[e.key === "ArrowDown" ? (at + 1) % items.length : at <= 0 ? items.length - 1 : at - 1]?.focus();
    e.preventDefault();
  }

  return (
    <>
      <button ref={button} type="button" className="av-menu__trigger" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}>
        {trigger}
        {!noChevron && CHEVRON}
      </button>
      {open && (
        <div id={id} ref={panel} className="av-menu" role="group" aria-label={label} onKeyDown={onKey} {...(POPOVER ? ({ popover: "manual" } as object) : {})}>
          {children}
        </div>
      )}
    </>
  );
}

/** A labelled run of rows; a hairline separates it from the next. */
export function MenuSection({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="av-menu__section">
      {label && <span className="av-menu__label">{label}</span>}
      {children}
    </div>
  );
}

export interface MenuItemProps {
  children: ReactNode;
  /** Shows a tick and `aria-pressed` (a choice that is on). */
  pressed?: boolean;
  /** Text on the right: a key or a count. */
  shortcut?: string;
  onClick?: () => void;
  disabled?: boolean;
}

/** One row, 48-52px tall. Rows with no `pressed` keep the tick's space so the words line up. */
export function MenuItem({ children, pressed, shortcut, onClick, disabled }: MenuItemProps) {
  return (
    <button type="button" className="av-menu__item" {...(pressed === undefined ? {} : { "aria-pressed": pressed })} disabled={disabled} onClick={onClick}>
      <span className="av-menu__tick">{pressed ? TICK : null}</span>
      <span className="av-menu__text">{children}</span>
      {shortcut && <span className="av-menu__shortcut">{shortcut}</span>}
    </button>
  );
}

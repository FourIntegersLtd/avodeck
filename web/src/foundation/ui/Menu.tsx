import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

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
  /** Let the caller hold the open state (for a keyboard shortcut that opens it). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** An id and classes for the trigger button, for a bar that styles it as one of its own tools. */
  triggerId?: string;
  triggerClassName?: string;
  /** Called as the menu closes and focus is about to go back to the trigger; `preventDefault()` keeps focus where the caller put it. */
  onCloseFocus?: (e: Event) => void;
}

/**
 * A quiet trigger and a menu of sections and rows, on the Radix dropdown primitive: `role="menu"`,
 * arrow keys, Home/End, type-ahead, Tab or Escape closes it, and focus goes back to the trigger.
 * Full width at the bottom on narrow screens (CSS).
 */
// Focus the first row, whether it was opened by pointer or key. (The prop reaches the menu content at runtime but is not in the package types.)
const openFocus = {
  onOpenAutoFocus: (e: Event) => {
    e.preventDefault();
    (e.currentTarget as HTMLElement).querySelector<HTMLElement>("[role^=menuitem]:not([data-disabled])")?.focus();
  },
} as Record<string, unknown>;

export function Menu({ label, trigger, children, noChevron, open, onOpenChange, triggerId, triggerClassName, onCloseFocus }: MenuProps) {
  const [inner, setInner] = useState(false);
  const isOpen = open ?? inner;
  const change = (next: boolean) => {
    setInner(next);
    onOpenChange?.(next);
  };
  return (
    <DropdownMenu.Root modal={false} open={isOpen} onOpenChange={change}>
      <DropdownMenu.Trigger id={triggerId} className={triggerClassName ?? "av-menu__trigger"}>
        {trigger}
        {!noChevron && CHEVRON}
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="av-menu"
          aria-label={label}
          aria-labelledby={undefined}
          align="end"
          sideOffset={8}
          collisionPadding={8}
          onCloseAutoFocus={onCloseFocus}
          {...openFocus}
          onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
            // Tab leaves the menu rather than being trapped in it.
            if (e.key === "Tab") change(false);
          }}
        >
          {children}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/** A labelled run of rows; a hairline separates it from the next. */
export function MenuSection({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <DropdownMenu.Group className="av-menu__section" aria-label={label}>
      {label && <DropdownMenu.Label className="av-menu__label">{label}</DropdownMenu.Label>}
      {children}
    </DropdownMenu.Group>
  );
}

export interface MenuItemProps {
  children: ReactNode;
  /** A choice that is on or off: shows a tick and `aria-checked` (a checkbox row). */
  pressed?: boolean;
  /** Text on the right: a key or a count. */
  shortcut?: string;
  /** An icon before the words. */
  icon?: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  /** Keep the menu open after the row is chosen. */
  keepOpen?: boolean;
  "aria-expanded"?: boolean;
  "aria-controls"?: string;
}

/** One row, 48-52px tall. Rows with no `pressed` keep the tick's space so the words line up. */
export function MenuItem({ children, pressed, shortcut, icon, onClick, disabled, keepOpen, ...aria }: MenuItemProps) {
  const onSelect = (e: Event) => {
    if (keepOpen) e.preventDefault();
    onClick?.();
  };
  const body = (
    <>
      {icon && <span className="av-menu__icon">{icon}</span>}
      <span className="av-menu__tick">{pressed ? TICK : null}</span>
      <span className="av-menu__text">{children}</span>
      {shortcut && <span className="av-menu__shortcut">{shortcut}</span>}
    </>
  );
  if (pressed === undefined) {
    return (
      <DropdownMenu.Item className="av-menu__item" disabled={disabled} onSelect={onSelect} {...aria}>
        {body}
      </DropdownMenu.Item>
    );
  }
  return (
    <DropdownMenu.CheckboxItem className="av-menu__item" checked={pressed} disabled={disabled} onSelect={onSelect} {...aria}>
      {body}
    </DropdownMenu.CheckboxItem>
  );
}

/** A row that goes to another screen. */
export function MenuLink({ to, icon, children }: { to: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <DropdownMenu.Item asChild>
      <Link to={to} className="av-menu__item">
        {icon && <span className="av-menu__icon">{icon}</span>}
        <span className="av-menu__tick" />
        <span className="av-menu__text">{children}</span>
      </Link>
    </DropdownMenu.Item>
  );
}

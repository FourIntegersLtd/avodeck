// S03: "How a case works", a walkthrough with one step per click. The steps are the pack's step keys.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { t } from "../../foundation/i18n";
import { Button, Dialog, stepLabel } from "../../foundation/ui";
import { Icon } from "./shared";
import "./how.css";

/** With no case there is no pack to ask, so the order comes from the language file (key `today.how.order`). */
function defaultOrder(): string[] {
  return t("today.how.order").split(",").map((k) => k.trim()).filter(Boolean);
}

export function HowCaseWorks({ steps }: { steps?: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="quiet" onClick={() => setOpen(true)} aria-haspopup="dialog">
        <Icon d="M9.5 9a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1 1-1.1 1.8M12 17h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" />
        {t("today.how.open")}
      </Button>
      {open && <Walkthrough keys={steps && steps.length > 0 ? steps : defaultOrder()} onClose={() => setOpen(false)} />}
    </>
  );
}

function Walkthrough({ keys, onClose }: { keys: string[]; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [direction, setDirection] = useState<"forward" | "back">("forward");
  const body = useRef<HTMLDivElement>(null);
  const last = index === keys.length - 1;
  const key = keys[index]!;
  const text = t(`today.how.step.${key}.body`, {}, "");

  const go = (to: number) => {
    if (to < 0 || to >= keys.length) return;
    setDirection(to > index ? "forward" : "back");
    setIndex(to);
  };
  // The button that was pressed may have gone (Back on step two); keep focus in the box.
  useEffect(() => {
    if (!body.current?.contains(document.activeElement)) body.current?.querySelector<HTMLElement>("[data-primary]")?.focus();
  }, [index]);
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") go(index + 1);
    if (e.key === "ArrowLeft") go(index - 1);
  };

  return (
    <Dialog title={t("today.how.title")} onClose={onClose} className="av-dialog--how">
      <div ref={body} className="av-how-walk" onKeyDown={onKey}>
        <p className="av-muted">{t("today.how.intro")}</p>
        <div className="av-how-walk__bar" aria-hidden="true">
          {keys.map((k, i) => (
            <span key={k} className={`av-how-walk__seg${i <= index ? " av-how-walk__seg--on" : ""}`} />
          ))}
        </div>
        <p className="av-how-walk__count">{t("today.how.step_of", { number: index + 1, total: keys.length })}</p>
        <div key={index} className={`av-how-walk__step av-how-walk__step--${direction}`} aria-live="polite">
          <span className="av-how-walk__number" aria-hidden="true">{index + 1}</span>
          <div>
            <h3>{t(`today.how.step.${key}.title`, {}, stepLabel(key))}</h3>
            {text && <p>{text}</p>}
          </div>
        </div>
        {last && <p className="av-how-walk__never">{t("today.how.never")}</p>}
        <div className="av-how-walk__actions">
          <Button variant="link" onClick={onClose}>{t("today.how.close")}</Button>
          <span className="av-how-walk__nav">
            {index > 0 && (
              <Button variant="secondary" arrow="back" onClick={() => go(index - 1)}>
                {t("today.how.back")}
              </Button>
            )}
            <Button data-primary arrow={!last} onClick={() => (last ? onClose() : go(index + 1))}>
              {t(last ? "today.how.done" : "today.how.next")}
            </Button>
          </span>
        </div>
      </div>
    </Dialog>
  );
}

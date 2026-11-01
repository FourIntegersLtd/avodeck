import { t } from "../i18n";
import { Button } from "./Button";
import { ZOOM_MAX, ZOOM_MIN, type Fit } from "./pageView";

export interface PageViewTools {
  zoom?: boolean;
  turn?: boolean;
  fit?: boolean;
  /** Brightness and contrast sliders. */
  clearer?: boolean;
}

export interface PageViewToolbarProps {
  /** The page's drawn width as a share of the space, in percent. */
  percent: number;
  /** Which tools show; all by default. Turn is the one a screen gates on what the person may do. */
  tools?: PageViewTools;
  fit?: Fit;
  brightness?: number;
  contrast?: number;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onReset?: () => void;
  onTurnLeft?: () => void;
  onTurnRight?: () => void;
  onFit?: (fit: Fit) => void;
  onBrightness?: (value: number) => void;
  onContrast?: (value: number) => void;
}

/** A controlled bar of page tools: the screen owns the state (usePageView) and passes it in. */
export function PageViewToolbar({ percent, tools, fit = "width", brightness = 100, contrast = 100, onZoomIn, onZoomOut, onReset, onTurnLeft, onTurnRight, onFit, onBrightness, onContrast }: PageViewToolbarProps) {
  const show = { zoom: true, turn: true, fit: true, clearer: true, ...tools };
  const tool = (label: string, onClick: (() => void) | undefined, extra: { disabled?: boolean; pressed?: boolean; text?: string } = {}) => (
    <Button variant="secondary" className="av-pageview__tool" aria-label={label} aria-pressed={extra.pressed} disabled={extra.disabled} onClick={onClick}>
      {extra.text ?? label}
    </Button>
  );
  return (
    <div className="av-pageview__bar" role="toolbar" aria-label={t("viewer.bar")}>
      {show.zoom && (
        <div className="av-pageview__group">
          {tool(t("viewer.zoom_out"), onZoomOut, { disabled: percent <= ZOOM_MIN, text: "−" })}
          <span className="av-pageview__level" aria-live="polite">
            {t("viewer.zoom_level", { percent })}
          </span>
          {tool(t("viewer.zoom_in"), onZoomIn, { disabled: percent >= ZOOM_MAX, text: "+" })}
        </div>
      )}
      {show.turn && (
        <div className="av-pageview__group">
          {tool(t("viewer.turn_left"), onTurnLeft)}
          {tool(t("viewer.turn_right"), onTurnRight)}
        </div>
      )}
      {show.fit && (
        <div className="av-pageview__group">
          {tool(t("viewer.fit_width"), () => onFit?.("width"), { pressed: fit === "width" })}
          {tool(t("viewer.whole_page"), () => onFit?.("page"), { pressed: fit === "page" })}
        </div>
      )}
      {show.clearer && (
        <div className="av-pageview__group av-pageview__sliders">
          <label>
            {t("viewer.brightness")}
            <input type="range" min={50} max={200} value={brightness} aria-label={t("viewer.brightness")} onChange={(e) => onBrightness?.(Number(e.target.value))} />
          </label>
          <label>
            {t("viewer.contrast")}
            <input type="range" min={50} max={200} value={contrast} aria-label={t("viewer.contrast")} onChange={(e) => onContrast?.(Number(e.target.value))} />
          </label>
        </div>
      )}
      {onReset && tool(t("viewer.reset"), onReset)}
    </div>
  );
}

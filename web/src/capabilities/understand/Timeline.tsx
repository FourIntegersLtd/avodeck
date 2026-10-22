// S11 The timeline. Lanes, their order and their words come from the API (the case's pack); this screen only
// draws them. Filters (window, status, question, day) are sent to the server, which does the filtering.
// The ribbon shows the whole record; the board or the list shows the window; the side panel shows one entry.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { ChoicePills, LinkButton, LoadError, Loading, Segmented, ToggleSwitch } from "../../foundation/ui";
import { chronologyEntries } from "./chronologyEntries";
import { casePath, GuidanceChanged } from "./shared";
import { splitForTrack, TimelineBoard } from "./TimelineBoard";
import { TimelineChronology } from "./TimelineChronology";
import { TimelineInspector } from "./TimelineInspector";
import { HighlightProvider } from "./highlight";
import { useRibbonData } from "./ribbonData";
import { TimelineRibbon } from "./TimelineRibbon";
import { lastTwoYearsWindow, periodDefaultDates } from "./timelineTimeWindow";
import { w } from "./words";

export { axisIso } from "./shared";
export { splitForTrack } from "./TimelineBoard";

type Event = Schemas["Event"];
type ViewMode = "board" | "list";
type WindowPreset = "period" | "whole" | "last_two" | "custom";

export function Timeline() {
  const { caseId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [windowPreset, setWindowPreset] = useState<WindowPreset>("period");
  const [periodSeeded, setPeriodSeeded] = useState(false);
  const [seenOn, setSeenOn] = useState("");
  const [dayOn, setDayOn] = useState(false);
  const [view, setView] = useState<ViewMode>("board");
  const [here, setHere] = useState<Event[]>([]);
  const opener = useRef<Element | null>(null);
  const lastCounts = useRef<Schemas["TimelineCounts"] | null>(null);
  const day = /^\d{4}-\d{2}-\d{2}$/.test(seenOn) ? seenOn : "";

  const asked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId]);
  const caseRow = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", casePath(caseId))), [caseId]);
  // The ribbon always shows the whole record (as it stood on the chosen day), whatever the window and filters.
  const whole = useRibbonData(caseId, day);
  const recordRange = whole.data?.range ?? null;
  // Until the instruction period (or, without one, the last two years) is applied, the board neither asks for
  // nor draws anything: it would show the record's own span and then jump.
  const windowReady =
    windowPreset !== "period" ||
    periodSeeded ||
    (!caseRow.loading && !whole.loading && periodDefaultDates(caseRow.data, recordRange) === null);
  const found = useResource(async () => {
    if (!windowReady) return null;
    const query: Record<string, string> = {};
    if (status === "verified" || status === "suggested") query.status = status;
    if (status === "not_linked" || status === "things_to_look") query.only = status;
    if (day) query.as_seen_on = day;
    if (questionId) query.question_id = questionId;
    if (from) query.from = from;
    if (to) query.to = to;
    return unwrap(api.GET("/v1/cases/{case_id}/timeline", { params: { path: { case_id: caseId }, query: query as never } }));
  }, [caseId, status, questionId, from, to, day, windowReady]);

  useEffect(() => {
    if (windowPreset !== "period" || periodSeeded) return;
    const dates = periodDefaultDates(caseRow.data, recordRange);
    if (dates) {
      setFrom(dates.from);
      setTo(dates.to);
      setPeriodSeeded(true);
    }
  }, [windowPreset, periodSeeded, caseRow.data, recordRange]);

  const choosePreset = (preset: WindowPreset) => {
    setWindowPreset(preset);
    if (preset === "whole") {
      setFrom("");
      setTo("");
    } else if (preset === "period") {
      setPeriodSeeded(false);
    } else if (preset === "last_two" && recordRange) {
      const dates = lastTwoYearsWindow(recordRange);
      setFrom(dates.from);
      setTo(dates.to);
    }
  };

  const data = found.data ?? undefined;
  const order = useMemo(() => (data ? chronologyEntries(splitForTrack(data).lanes, splitForTrack(data).unplaced) : []), [data]);
  const entryId = params.get("entry");
  const current = entryId ? order.find((e) => e.fact_id === entryId) ?? null : null;
  const panelHere = current && here.some((e) => e.fact_id === current.fact_id) ? here : current ? [current] : [];

  const open = useCallback(
    (events: Event[], at?: string) => {
      opener.current = document.activeElement;
      setHere(events);
      const next = new URLSearchParams(params);
      next.set("entry", at ?? events[0]!.fact_id);
      setParams(next);
    },
    [params, setParams],
  );
  const select = (event: Event) => {
    const next = new URLSearchParams(params);
    next.set("entry", event.fact_id);
    setParams(next, { replace: true });
  };
  const close = useCallback(() => {
    const id = params.get("entry");
    const next = new URLSearchParams(params);
    next.delete("entry");
    setParams(next);
    setHere([]);
    const back = opener.current?.isConnected ? opener.current : id ? document.querySelector(`[data-facts~="${id}"]`) : null;
    if (back instanceof HTMLElement) requestAnimationFrame(() => back.focus());
    opener.current = null;
  }, [params, setParams]);

  const c = data?.counts ?? lastCounts.current;
  if (data?.counts) lastCounts.current = data.counts;
  const numbers = Object.fromEntries((asked.data?.questions ?? []).map((q) => [q.id, q.number]));
  const seenTitle = data?.as_seen_title ?? w("consultation.on_label");
  const switchDay = (on: boolean) => {
    setDayOn(on);
    if (!on) setSeenOn("");
  };
  const split = data ? splitForTrack(data) : null;
  const undated = data?.counts?.undated ?? 0;
  const showUndatedBanner = !!split && undated > 0 && !split.lanes.some((l) => l.events.length > 0) && split.unplaced.length > 0;

  return (
    <HighlightProvider>
    <div className="av-page av-page--wide av-stack">
      <div className="av-understand-top">
        <div>
          <h2>{w("timeline.title")}</h2>
          <p className="av-muted">{w("timeline.intro")}</p>
          <p className="av-muted">{w("timeline.blind_read_hint")}</p>
        </div>
        <div className="av-understand-top__tools">
          <ToggleSwitch label={w("timeline.as_seen_toggle", { title: seenTitle })} checked={dayOn} onChange={switchDay} />
          {dayOn && (
            <div className="av-field">
              <label className="av-field__label" htmlFor="tl-seen">{w("timeline.as_seen_on", { title: seenTitle })}</label>
              <input id="tl-seen" className="av-input" type="date" value={seenOn} onChange={(e) => setSeenOn(e.target.value)} />
            </div>
          )}
        </div>
      </div>

      <div className="av-story-window" aria-label={w("timeline.window")} role="group">
        <div className="av-story-window__row">
          <Segmented
            label={w("timeline.window")}
            hideLabel
            value={windowPreset}
            onChange={(v) => choosePreset(v as WindowPreset)}
            options={[
              { value: "period", label: w("timeline.window.period") },
              { value: "whole", label: w("timeline.window.whole") },
              { value: "last_two", label: w("timeline.window.last_two") },
              { value: "custom", label: w("timeline.window.custom") },
            ]}
          />
          {windowPreset === "custom" && (
            <form className="av-understand-time-window__dates" onSubmit={(e) => e.preventDefault()}>
              <div className="av-field">
                <label className="av-field__label" htmlFor="tl-from">{w("timeline.filter.from")}</label>
                <input id="tl-from" className="av-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </div>
              <div className="av-field">
                <label className="av-field__label" htmlFor="tl-to">{w("timeline.filter.to")}</label>
                <input id="tl-to" className="av-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </div>
            </form>
          )}
        </div>
        {whole.data && (
          <TimelineRibbon
            data={whole.data}
            from={from}
            to={to}
            onChange={(f, t2) => {
              setWindowPreset("custom");
              setFrom(f);
              setTo(t2);
            }}
          />
        )}
      </div>

      <div className="av-story-controls">
        <Segmented
          label={w("timeline.view")}
          hideLabel
          value={view}
          onChange={(v) => setView(v as ViewMode)}
          options={[
            { value: "board", label: w("timeline.view.board") },
            { value: "list", label: w("timeline.view.list") },
          ]}
        />
        {c && (
        <ChoicePills
          label={w("timeline.filter.status")}
          hideLabel
          value={status}
          onChange={setStatus}
          options={[
            { value: "", label: w("timeline.filter.status.all") },
            { value: "verified", label: w("timeline.count", { label: t("ui.status.checked"), n: c.checked }) },
            { value: "suggested", label: w("timeline.count", { label: t("ui.status.please_check"), n: c.please_check }) },
            { value: "things_to_look", label: w("timeline.count", { label: t("ui.status.look"), n: c.things_to_look }) },
            { value: "not_linked", label: w("timeline.count", { label: w("timeline.not_linked"), n: c.not_linked }) },
          ]}
        />
        )}
        <ChoicePills
          label={w("timeline.filter.question")}
          hideLabel
          value={questionId}
          onChange={setQuestionId}
          options={[
            { value: "", label: w("timeline.filter.question.all") },
            ...(asked.data?.questions ?? []).map((q) => ({ value: q.id, label: t("ui.qchip", { n: q.number }) })),
          ]}
        />
      </div>
      {questionId && numbers[questionId] !== undefined && (
        <p className="av-understand-onlychip">
          <span className="av-understand-onlychip__pill">
            {w("timeline.question_only", { n: numbers[questionId] })}
            <button type="button" className="av-understand-onlychip__clear" aria-label={w("timeline.question_clear")} onClick={() => setQuestionId("")}>
              <span aria-hidden="true">×</span>
            </button>
          </span>
        </p>
      )}

      {found.error ? (
        <LoadError onRetry={found.reload} />
      ) : data && split ? (
        <div className={`av-story-layout${current ? " av-story-layout--open" : ""}`}>
          <div className="av-stack av-story-layout__main">
            <GuidanceChanged codes={data.guidance_changed} />
            {showUndatedBanner && (
              <div className="av-card av-understand-undated-banner" data-testid="timeline-undated-banner">
                <p>{w("timeline.undated_banner", { count: undated })}</p>
                <LinkButton to={screenPath("reader", { caseId }) ?? `/cases/${caseId}`} arrow>
                  {w("timeline.undated_banner_link")}
                </LinkButton>
              </div>
            )}
            {view === "board" ? (
              <TimelineBoard caseId={caseId} data={data} from={from} to={to} numbers={numbers} selectedId={current?.fact_id} onOpen={open} />
            ) : (
              <TimelineChronology entries={order} numbers={numbers} selectedId={current?.fact_id} onOpen={open} />
            )}
          </div>
          {current && <TimelineInspector caseId={caseId} current={current} here={panelHere} order={order} numbers={numbers} onSelect={select} onClose={close} />}
        </div>
      ) : (
        <Loading />
      )}
    </div>
    </HighlightProvider>
  );
}

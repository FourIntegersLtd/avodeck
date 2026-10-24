// S15 Your opinion (docs/design/screens/S15-Opinion.dc.html): the expert's own words on one question.
// Clicking a fact puts a marker in the text and keeps the server's own {fact_id, page_id, quote_verbatim}
// for it; the browser never builds a quote (rule 2). Nothing here writes or rewrites the opinion (rule 1).
// It saves by itself (If-Match on the version), asks which version to keep when another window saved first,
// shows the checks (warnings only) and lists earlier versions.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import {
  api,
  ApiError,
  ifMatch,
  unwrap,
  type Schemas,
} from "../../foundation/platform-client";
import {
  Button,
  Checkbox,
  StaleChoice,
  DictationBar,
  LinkButton,
  LoadError,
  Loading,
  Notice,
  PageRef,
  SegmentedControl,
} from "../../foundation/ui";
import {
  ReasonDialog,
  versionTag,
  WithQuestion,
  KindBadge,
  type Chosen,
} from "./shared";
import { w } from "./words";

type Workspace = Schemas["Workspace"];
type OpinionOut = Schemas["OpinionOut"];
type Citation = Schemas["Citation"];
type Fields = {
  body: string;
  refs: Citation[];
  view: string | null;
  provisional: boolean;
  test: string;
};
type Status = "saved" | "unsaved" | "saving" | "failed" | "stopped";
const MARKER = /\[\[cite:(\d+)\]\]/g;

/** Drop the citations whose marker is gone from the text and renumber the rest in their order. */
export function prune(body: string, refs: Citation[]): { body: string; refs: Citation[] } {
  const used = new Set([...body.matchAll(MARKER)].map((m) => Number(m[1])));
  const kept: Citation[] = [];
  const renumber = new Map<number, number>();
  refs.forEach((ref, i) => {
    if (used.has(i + 1)) {
      kept.push(ref);
      renumber.set(i + 1, kept.length);
    }
  });
  if (kept.length === refs.length) return { body, refs };
  return {
    body: body.replace(MARKER, (whole, n: string) => {
      const next = renumber.get(Number(n));
      return next === undefined ? whole : `[[cite:${next}]]`;
    }),
    refs: kept,
  };
}

/** The plain words for a citation the server refused (rule 2): which one, and what to do. */
function refusal(err: ApiError): string {
  const number = Number(err.extra.citation ?? err.extra.marker ?? 0);
  const reason = String(err.extra.reason ?? "");
  if (reason === "unused_citation" || reason === "unknown_marker") return w("opinion.marker_refused", { number });
  if (number > 0) return w("opinion.cite_refused", { number });
  return w("opinion.cite_failed");
}
const AUTOSAVE_MS = 5000;
const ISSUE_TYPES = new Set([
  "uncited_fact",
  "loaded_language",
  "outside_expertise",
]);

const fieldsOf = (o: OpinionOut | null): Fields => ({
  body: o?.body ?? "",
  refs: o?.citation_refs ?? [],
  view: o?.view ?? null,
  provisional: o?.provisional ?? false,
  test: o?.test_text ?? "",
});
const dateOf = (iso: string, withTime = false) =>
  new Intl.DateTimeFormat(
    undefined,
    withTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "long", timeZone: "UTC" },
  ).format(new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso));

export function Opinion() {
  const { caseId = "" } = useParams();
  return (
    <WithQuestion caseId={caseId}>
      {(chosen) => (
        <Loader key={chosen.current.id} caseId={caseId} chosen={chosen} />
      )}
    </WithQuestion>
  );
}

function Loader({ caseId, chosen }: { caseId: string; chosen: Chosen }) {
  const found = useResource(
    () =>
      unwrap(
        api.GET("/v1/cases/{case_id}/questions/{question_id}/workspace", {
          params: { path: { case_id: caseId, question_id: chosen.current.id } },
        }),
      ),
    [caseId, chosen.current.id],
  );
  if (found.error)
    return (
      <div className="av-page">
        <LoadError onRetry={found.reload} />
      </div>
    );
  if (!found.data)
    return (
      <div className="av-page">
        <Loading />
      </div>
    );
  return <Editor caseId={caseId} chosen={chosen} space={found.data} />;
}

function Editor({
  caseId,
  chosen,
  space,
}: {
  caseId: string;
  chosen: Chosen;
  space: Workspace;
}) {
  const questionId = chosen.current.id;
  const question = chosen.current;
  const paraRef = space.question.para_ref ?? question.para_ref;
  const [fields, setFields] = useState(() => fieldsOf(space.opinion));
  const [issues, setIssues] = useState<Schemas["LintIssueOut"][]>(
    space.opinion?.lint ?? [],
  );
  const [status, setStatus] = useState<Status>("saved");
  const [message, setMessage] = useState<string | null>(null);
  const [stale, setStale] = useState<{ saved: OpinionOut | null } | null>(null);
  const [dismissing, setDismissing] = useState<Schemas["LintIssueOut"] | null>(
    null,
  );
  const [opinionId, setOpinionId] = useState(space.opinion?.id ?? null);
  const [showVersions, setShowVersions] = useState(false);
  const [versions, setVersions] = useState<Schemas["OpinionVersion"][] | null>(
    null,
  );
  const [current, setCurrent] = useState(space.opinion?.version ?? 0);
  const [tick, setTick] = useState(0);
  const [ownTest, setOwnTest] = useState(Boolean(space.opinion?.test_text));
  const [savedBody, setSavedBody] = useState(space.opinion?.body ?? "");
  const [changedRefs, setChangedRefs] = useState<number[]>(
    space.opinion?.changed_citations ?? [],
  );

  const latest = useRef(fields);
  latest.current = fields;
  const version = useRef(space.opinion?.version ?? 0);
  const changed = useRef(0);
  const saved = useRef(0);
  const busy = useRef(false);
  const stoppedAt = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const cursor = useRef<number | null>(null);
  const path = { case_id: caseId, question_id: questionId };

  const adopt = (o: OpinionOut) => {
    version.current = o.version;
    setCurrent(o.version);
    setOpinionId(o.id);
    setSavedBody(o.body);
    setIssues(o.lint ?? []);
    setChangedRefs(o.changed_citations ?? []);
  };

  const lintAgain = async () => {
    if (!version.current) return;
    try {
      setIssues(
        (
          await unwrap(
            api.POST(
              "/v1/cases/{case_id}/questions/{question_id}/opinion/lint",
              { params: { path } },
            ),
          )
        ).issues,
      );
    } catch {
      /* the checks are only advice; the next save brings them back */
    }
  };

  const save = useCallback(async (): Promise<void> => {
    if (
      busy.current ||
      saved.current === changed.current ||
      stoppedAt.current === changed.current
    )
      return;
    busy.current = true;
    const mark = changed.current;
    const f = latest.current;
    setStatus("saving");
    try {
      const out = await unwrap(
        api.PUT("/v1/cases/{case_id}/questions/{question_id}/opinion", {
          params: { path: { case_id: caseId, question_id: questionId } },
          body: {
            body: f.body,
            citation_refs: f.refs,
            view: f.view,
            provisional: f.provisional,
            test_text: f.test || null,
          },
          headers: ifMatch(versionTag(version.current)),
        }),
      );
      adopt(out);
      saved.current = mark;
      setMessage(null);
      setStatus(changed.current === mark ? "saved" : "unsaved");
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        setStale({ saved: (err.extra.saved as OpinionOut | null) ?? null });
        setStatus("unsaved");
      } else if (err instanceof ApiError && err.status === 422) {
        stoppedAt.current = mark;
        setMessage(refusal(err));
        setStatus("stopped");
      } else {
        setMessage(errorMessage(err, { invalid: "write.opinion.cite_failed" }));
        setStatus("failed");
        setTimeout(() => setTick((n) => n + 1), AUTOSAVE_MS);
      }
    } finally {
      busy.current = false;
    }
    if (changed.current !== saved.current && !stale) setTick((n) => n + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, questionId]);

  // Save five seconds after the last change (and again after a failure).
  useEffect(() => {
    if (changed.current === saved.current) return;
    const timer = setTimeout(() => void save(), AUTOSAVE_MS);
    return () => clearTimeout(timer);
  }, [tick, save]);

  const edit = (change: Partial<Fields>) => {
    const next = { ...latest.current, ...change };
    if (change.body !== undefined || change.refs !== undefined) {
      const tidy = prune(next.body, next.refs);
      const area = box.current?.querySelector("textarea");
      if (tidy.body !== next.body && cursor.current === null && area) {
        const at = area.selectionStart ?? next.body.length;
        cursor.current = Math.max(0, at - (next.body.length - tidy.body.length));
      }
      next.body = tidy.body;
      next.refs = tidy.refs;
    }
    setFields(next);
    latest.current = next;
    changed.current += 1;
    setStatus("unsaved");
    setTick((n) => n + 1);
  };

  const leave = async (e: React.FocusEvent) => {
    if (
      stale ||
      (e.relatedTarget as HTMLElement | null)?.closest(
        "[data-chips], [role=dialog]",
      )
    )
      return;
    const unsaved = changed.current !== saved.current;
    await save();
    if (unsaved) await lintAgain();
  };

  const insert = (chip: Schemas["Chip"]) => {
    const area = box.current?.querySelector("textarea");
    const at =
      area && area.selectionStart != null
        ? area.selectionStart
        : latest.current.body.length;
    const known = latest.current.refs.findIndex(
      (r) => r.fact_id === chip.fact_id,
    );
    const refs =
      known >= 0
        ? latest.current.refs
        : [
            ...latest.current.refs,
            {
              fact_id: chip.fact_id,
              page_id: chip.page_id,
              quote_verbatim: chip.quote_verbatim,
              fact_version: chip.fact_version,
            },
          ];
    const marker = `[[cite:${(known >= 0 ? known : refs.length - 1) + 1}]]`;
    const body = latest.current.body;
    cursor.current = at + marker.length;
    area?.focus();
    edit({ body: body.slice(0, at) + marker + body.slice(at), refs });
  };

  useEffect(() => {
    const area = box.current?.querySelector("textarea");
    if (area && cursor.current !== null) {
      area.setSelectionRange(cursor.current, cursor.current);
      cursor.current = null;
    }
  });

  const chipFor = (n: number) =>
    space.chips.find((c) => c.fact_id === latest.current.refs[n - 1]?.fact_id);
  const recite = (n: number) => {
    const found = chipFor(n);
    if (!found) return;
    const refs = latest.current.refs.map((r, i) =>
      i === n - 1
        ? {
            fact_id: found.fact_id,
            page_id: found.page_id,
            quote_verbatim: found.quote_verbatim,
            fact_version: found.fact_version,
          }
        : r,
    );
    edit({ refs });
    void save();
  };

  const keepSaved = () => {
    const mine = stale?.saved;
    setStale(null);
    if (mine) {
      adopt(mine);
      setFields(fieldsOf(mine));
      saved.current = changed.current;
      setStatus("saved");
    }
  };
  const keepMine = () => {
    version.current = stale?.saved?.version ?? 0;
    setStale(null);
    setTick((n) => n + 1);
    void save();
  };

  const loadVersions = useCallback(async () => {
    if (!opinionId) return setVersions([]);
    try {
      setVersions(
        (
          await unwrap(
            api.GET("/v1/cases/{case_id}/opinions/{opinion_id}/versions", {
              params: { path: { case_id: caseId, opinion_id: opinionId } },
            }),
          )
        ).versions,
      );
    } catch (err) {
      setMessage(errorMessage(err));
    }
  }, [caseId, opinionId]);
  useEffect(() => {
    if (showVersions) void loadVersions();
  }, [showVersions, loadVersions, current]);

  const restore = async (n: number) => {
    try {
      const out = await unwrap(
        api.POST(
          "/v1/cases/{case_id}/opinions/{opinion_id}/versions/{version_no}/restore",
          {
            params: {
              path: { case_id: caseId, opinion_id: opinionId!, version_no: n },
            },
            headers: ifMatch(versionTag(version.current)),
          },
        ),
      );
      adopt(out);
      setFields(fieldsOf(out));
      saved.current = changed.current;
      setStatus("saved");
      setMessage(w("opinion.restored"));
    } catch (err) {
      setMessage(errorMessage(err, { stale: "write.points.stale" }));
    }
  };

  const dismiss = async (issue: Schemas["LintIssueOut"], reason: string) => {
    try {
      await unwrap(
        api.PATCH("/v1/cases/{case_id}/lint-issues/{issue_id}", {
          params: { path: { case_id: caseId, issue_id: issue.id } },
          body: { dismissed_reason: reason },
        }),
      );
      setIssues((all) => all.filter((i) => i.id !== issue.id));
      setDismissing(null);
    } catch (err) {
      setMessage(errorMessage(err));
    }
  };

  const open = issues.filter((i) => i.status === "open");
  const sides = space.points.sides;
  return (
    <div className="av-page av-page--wide av-stack">
      <h2 className="av-visually-hidden">{w("opinion.title")}</h2>
      <div className="av-write-split">
        <aside className="av-stack" data-chips>
          <section className="av-card" aria-labelledby="chips-title" data-chips>
            <h3 id="chips-title">
              {w("opinion.facts", { number: question.number })}
            </h3>
            <p className="av-muted">{w("opinion.facts_hint")}</p>
            {space.chips.length === 0 && (
              <p className="av-muted">{w("opinion.facts_empty")}</p>
            )}
            {space.chips.map((chip) => (
              <button
                key={chip.fact_id}
                type="button"
                className="av-write-chip"
                onClick={() => insert(chip)}
              >
                <span>{chip.value}</span>
                <PageRef page={chip.page_no} />
              </button>
            ))}
          </section>
          {space.guidance.length > 0 && space.guidance_on && (
            <section
              className="av-card av-stack"
              aria-labelledby="guidance-title"
            >
              <h3 id="guidance-title">
                {w("opinion.guidance", { date: dateOf(space.guidance_on) })}
              </h3>
              {space.guidance.map((g) => (
                <div key={g.code}>
                  <p>
                    {g.url ? <a href={g.url}>{g.title}</a> : g.title}
                    <br />
                    <span className="av-muted">{g.source}</span>
                  </p>
                  <span className="av-write-badge">
                    {w("opinion.guidance_force")}
                  </span>
                </div>
              ))}
            </section>
          )}
          <section className="av-card av-stack" aria-labelledby="points-title">
            <h3 id="points-title">{w("opinion.points")}</h3>
            <p>
              {sides
                .map((s) =>
                  w("opinion.points_summary", {
                    side: s.label,
                    count: s.count,
                  }),
                )
                .join(" · ")}
              {space.points.differ > 0 &&
                ` · ${w("opinion.points_differ", { count: space.points.differ })}`}
            </p>
            <Link to={`/cases/${caseId}/points?q=${questionId}`}>
              {w("opinion.points_open")}
            </Link>
          </section>
        </aside>

        <div className="av-stack" onBlur={(e) => void leave(e)}>
          <div>
            <p className="av-muted">
              <KindBadge header={space.question} />{" "}
              {paraRef && (
                <span>{w("opinion.from_paragraph", { ref: paraRef })}</span>
              )}
            </p>
            <h3 className="av-write-question">{question.confirmed_text ?? question.text_verbatim}</h3>
          </div>
          {message && (
            <Notice
              tone={status === "failed" || status === "stopped" ? "error" : "info"}
            >
              <p>{message}</p>
            </Notice>
          )}
          {changedRefs.length > 0 && (
            <Notice tone="warning">
              {changedRefs.map((n) => (
                <div key={n} className="av-stack">
                  <p>{w("opinion.changed_since_cited", { number: n })}</p>
                  {chipFor(n) && (
                    <Button variant="secondary" onClick={() => recite(n)}>
                      {w("opinion.recite", { number: n })}
                    </Button>
                  )}
                </div>
              ))}
            </Notice>
          )}
          <section className="av-card av-write-paper">
            <div ref={box}>
              <DictationBar
                caseId={caseId}
                context="opinion"
                label={w("opinion.editor")}
                value={fields.body}
                onChange={(body) => edit({ body })}
                rows={10}
              />
            </div>
            <p className="av-muted">{w("opinion.yours_only")}</p>
          </section>

          <FlaggedCopy
            body={savedBody}
            issues={open}
            page={(n) => {
              const ref = (space.opinion?.citation_refs ?? latest.current.refs)[n - 1];
              return space.chips.find((c) => c.fact_id === ref?.fact_id)?.page_no;
            }}
          />

          <div className="av-write-columns">
            <section className="av-card av-stack">
              <h3>{w("opinion.view")}</h3>
              <SegmentedControl
                label={w("opinion.view")}
                value={fields.view}
                onChange={(view) => edit({ view })}
                options={space.views.map((v) => ({ value: v.code, label: v.label }))}
              />
            </section>
            <section className="av-card av-stack">
              <h3>{w("opinion.test")}</h3>
              <p className="av-write-test">{space.legal_test}</p>
              {ownTest ? (
                <div>
                  <label className="av-field__label" htmlFor="test-text">
                    {w("opinion.test")}
                  </label>
                  <textarea
                    id="test-text"
                    className="av-textarea"
                    rows={4}
                    value={fields.test}
                    onChange={(e) => edit({ test: e.target.value })}
                  />
                </div>
              ) : (
                <button type="button" className="av-text-link av-write-linkbutton" onClick={() => setOwnTest(true)}>
                  {w("opinion.test_own")}
                </button>
              )}
              <Checkbox
                label={w("opinion.provisional")}
                checked={fields.provisional}
                onChange={(e) => edit({ provisional: e.target.checked })}
              />
            </section>
          </div>

          <section className="av-card av-stack" aria-labelledby="checks-title">
            <h3 id="checks-title">{w("opinion.checks")}</h3>
            {open.map((issue) => (
              <div key={issue.id} className="av-write-item__head">
                <p>
                  {w(
                    ISSUE_TYPES.has(issue.type)
                      ? (`opinion.issue.${issue.type}` as "opinion.issue.uncited_fact")
                      : "opinion.issue.other",
                    { words: issue.words },
                  )}
                </p>
                {issue.type === "uncited_fact" && issue.sentence_start != null && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      const mark = document.getElementById(`flagged-${issue.sentence_start}`);
                      mark?.focus();
                      mark?.scrollIntoView?.({ block: "center" });
                    }}
                  >
                    {w("opinion.add_page")}
                  </Button>
                )}
                <Button
                  variant="secondary"
                  onClick={() => setDismissing(issue)}
                >
                  {w("opinion.issue_dismiss")}
                </Button>
              </div>
            ))}
            {(
              [
                ["uncited_fact", "opinion.check_uncited_ok"],
                ["loaded_language", "opinion.check_loaded_ok"],
                ["outside_expertise", "opinion.check_expertise_ok"],
              ] as const
            )
              .filter(([type]) => !open.some((i) => i.type === type))
              .map(([type, key]) => (
                <p key={type} className="av-write-pass">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12l5 5 9-10" />
                  </svg>
                  {w(key)}
                </p>
              ))}
          </section>

          <div className="av-write-footer av-write-footer--between">
            <p role="status" className="av-muted">
              <span>{status === "saving"
                ? w("opinion.saving")
                : status === "saved"
                  ? w("opinion.saved")
                  : status === "failed"
                    ? w("opinion.save_failed")
                    : status === "stopped"
                      ? w("opinion.save_stopped")
                      : w("opinion.unsaved")}</span>
              {chosen.progress && (
                <>
                  {" · "}
                  <span>
                    {w("opinion.answered", {
                      answered: chosen.progress.answered + (opinionId && !space.opinion ? 1 : 0),
                      total: chosen.progress.total,
                    })}
                  </span>
                </>
              )}
              {" · "}
              <button type="button" className="av-text-link av-write-linkbutton" aria-expanded={showVersions} onClick={() => setShowVersions((v) => !v)}>
                {w("opinion.versions")}
              </button>
            </p>
            {chosen.next ? (
              <LinkButton arrow to={`/cases/${caseId}/opinion?q=${chosen.next.id}`}>
                {w("opinion.next")}
              </LinkButton>
            ) : (
              <>
                <LinkButton arrow to={screenPath("S16", { caseId }) ?? `/cases/${caseId}/report`}>
                  {w("opinion.go_report")}
                </LinkButton>
                <LinkButton variant="secondary" to={`/cases/${caseId}/points?q=${questionId}`}>
                  {w("opinion.back_to_points")}
                </LinkButton>
              </>
            )}
          </div>

          {showVersions && (
            <section
              className="av-card av-stack"
              aria-labelledby="versions-title"
            >
              <h3 id="versions-title">{w("opinion.versions")}</h3>
              {versions && versions.length === 0 && (
                <p className="av-muted">{w("opinion.versions_empty")}</p>
              )}
              {versions?.map((v) => (
                <div key={v.version_no} className="av-write-item">
                  <p className="av-muted">
                    {w("opinion.version_item", {
                      number: v.version_no,
                      when: dateOf(v.saved_at, true),
                    })}
                  </p>
                  <p className="av-write-excerpt">{v.body}</p>
                  {v.version_no !== current && (
                    <Button
                      variant="secondary"
                      onClick={() => restore(v.version_no)}
                    >
                      {w("opinion.restore", { number: v.version_no })}
                    </Button>
                  )}
                </div>
              ))}
            </section>
          )}
        </div>
      </div>
      {dismissing && (
        <ReasonDialog
          title={w("opinion.dismiss_title")}
          label={w("opinion.dismiss_reason")}
          confirm={w("opinion.dismiss_confirm")}
          onConfirm={(reason) => dismiss(dismissing, reason)}
          onClose={() => setDismissing(null)}
        />
      )}
      {stale && (
        <StaleChoice
          draft={latest.current.body}
          saved={stale.saved?.body ?? null}
          savedVersion={stale.saved?.version}
          onKeepMine={keepMine}
          onUseSaved={keepSaved}
          onClose={() => setStale(null)}
          words={{
            title: w("opinion.stale_title"),
            keepMine: w("opinion.stale_yours"),
            useSaved: w("opinion.stale_saved"),
          }}
        />
      )}
    </div>
  );
}

/** The saved text read-only, with each sentence that has a check marked (citation markers shown as pages). */
function FlaggedCopy({ body, issues, page }: { body: string; issues: Schemas["LintIssueOut"][]; page: (n: number) => number | undefined }) {
  const spans = issues
    .filter((i) => i.sentence_start != null && i.sentence_end != null && i.sentence_end <= body.length)
    .map((i) => [i.sentence_start as number, i.sentence_end as number] as const)
    .sort((a, b) => a[0] - b[0]);
  const plain = (text: string) => text.replace(MARKER, (_, n: string) => (page(Number(n)) ? w("points.page", { page: page(Number(n))! }) : `[${n}]`));
  if (spans.length === 0) {
    if (!body.includes("[[cite:")) return null;
    return (
      <section className="av-card" aria-label={w("opinion.readable_copy")}>
        <p className="av-write-copy">{plain(body)}</p>
      </section>
    );
  }
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const [start, end] of spans) {
    if (start < at) continue;
    if (start > at) parts.push(<span key={`t${at}`}>{plain(body.slice(at, start))}</span>);
    parts.push(<mark key={`m${start}`} id={`flagged-${start}`} tabIndex={-1} className="av-write-flagged">{plain(body.slice(start, end))}</mark>);
    at = end;
  }
  if (at < body.length) parts.push(<span key={`t${at}`}>{plain(body.slice(at))}</span>);
  return (
    <section className="av-card" aria-label={w("opinion.flagged_copy")}>
      <p className="av-write-copy">{parts}</p>
    </section>
  );
}

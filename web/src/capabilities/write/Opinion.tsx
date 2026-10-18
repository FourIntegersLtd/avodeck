// S15 Your opinion (docs/design/screens/S15-Opinion.dc.html): the expert's own words on one question.
// Clicking a fact puts a marker in the text and keeps the server's own {fact_id, page_id, quote_verbatim}
// for it; the browser never builds a quote (rule 2). Nothing here writes or rewrites the opinion (rule 1).
// It saves by itself (If-Match on the version), asks which version to keep when another window saved first,
// shows the checks (warnings only) and lists earlier versions.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
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
  Dialog,
  DictationBox,
  LinkButton,
  LoadError,
  Loading,
  Notice,
} from "../../foundation/ui";
import {
  pageLabel,
  ReasonDialog,
  versionTag,
  WithQuestion,
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
type Status = "saved" | "unsaved" | "saving" | "failed";
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

  const latest = useRef(fields);
  latest.current = fields;
  const version = useRef(space.opinion?.version ?? 0);
  const changed = useRef(0);
  const saved = useRef(0);
  const busy = useRef(false);
  const box = useRef<HTMLDivElement>(null);
  const cursor = useRef<number | null>(null);
  const path = { case_id: caseId, question_id: questionId };

  const adopt = (o: OpinionOut) => {
    version.current = o.version;
    setCurrent(o.version);
    setOpinionId(o.id);
    setIssues(o.lint ?? []);
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
    if (busy.current || saved.current === changed.current) return;
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

  const edit = (next: Partial<Fields>) => {
    setFields({ ...latest.current, ...next });
    latest.current = { ...latest.current, ...next };
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
    <div className="av-page av-stack">
      <h2>{w("opinion.title")}</h2>
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
                <span className="av-write-page">{pageLabel(chip.page_no)}</span>
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
              <span className="av-write-badge">Q{question.number}</span>
            </p>
            <h3>{question.confirmed_text ?? question.text_verbatim}</h3>
          </div>
          {message && (
            <Notice tone={status === "failed" ? "error" : "info"}>
              <p>{message}</p>
            </Notice>
          )}
          <section className="av-card av-stack">
            <div ref={box}>
              <DictationBox
                caseId={caseId}
                context="opinion"
                label={w("opinion.editor")}
                value={fields.body}
                onChange={(body) => edit({ body })}
                rows={12}
              />
            </div>
            <p className="av-muted">{w("opinion.yours_only")}</p>
          </section>

          <div className="av-write-columns">
            <fieldset className="av-card av-write-fieldset">
              <legend>{w("opinion.view")}</legend>
              {space.views.map((v) => (
                <label key={v.code} className="av-write-radio">
                  <input
                    type="radio"
                    name="view"
                    checked={fields.view === v.code}
                    onChange={() => edit({ view: v.code })}
                  />
                  <span>{v.label}</span>
                </label>
              ))}
            </fieldset>
            <section className="av-card av-stack">
              <h3>
                <label htmlFor="test-text">{w("opinion.test")}</label>
              </h3>
              <textarea
                id="test-text"
                className="av-textarea"
                rows={4}
                value={fields.test}
                onChange={(e) => edit({ test: e.target.value })}
              />
              <p className="av-muted">
                {w("opinion.test_reference")} <span>{space.legal_test}</span>
              </p>
              <Checkbox
                label={w("opinion.provisional")}
                checked={fields.provisional}
                onChange={(e) => edit({ provisional: e.target.checked })}
              />
            </section>
          </div>

          <section className="av-card av-stack" aria-labelledby="checks-title">
            <h3 id="checks-title">{w("opinion.checks")}</h3>
            {open.length === 0 && (
              <p className="av-muted">{w("opinion.checks_clear")}</p>
            )}
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
                <Button
                  variant="secondary"
                  onClick={() => setDismissing(issue)}
                >
                  {w("opinion.issue_dismiss")}
                </Button>
              </div>
            ))}
          </section>

          <div className="av-write-footer">
            <p role="status" className="av-muted">
              {status === "saving"
                ? w("opinion.saving")
                : status === "saved"
                  ? w("opinion.saved")
                  : status === "failed"
                    ? w("opinion.save_failed")
                    : w("opinion.unsaved")}
            </p>
            <Button
              variant="secondary"
              aria-expanded={showVersions}
              onClick={() => setShowVersions((s) => !s)}
            >
              {w("opinion.versions")}
            </Button>
            {chosen.next ? (
              <LinkButton to={`/cases/${caseId}/opinion?q=${chosen.next.id}`}>
                {w("opinion.next")}
              </LinkButton>
            ) : (
              <LinkButton to={`/cases/${caseId}/points?q=${questionId}`}>
                {w("opinion.back_to_points")}
              </LinkButton>
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
                      onClick={() => void restore(v.version_no)}
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
          onConfirm={(reason) => void dismiss(dismissing, reason)}
          onClose={() => setDismissing(null)}
        />
      )}
      {stale && (
        <Dialog title={w("opinion.stale_title")} onClose={() => setStale(null)}>
          <div className="av-stack">
            <p className="av-write-excerpt">{latest.current.body}</p>
            <Button onClick={keepMine}>{w("opinion.stale_yours")}</Button>
            {stale.saved && (
              <p className="av-write-excerpt">{stale.saved.body}</p>
            )}
            <Button variant="secondary" onClick={keepSaved}>
              {w("opinion.stale_saved")}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

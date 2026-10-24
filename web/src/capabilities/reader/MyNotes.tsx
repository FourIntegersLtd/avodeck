// The expert's own bookmarks and private notes (screen 09). Only the person who keeps them sees them;
// they are separate from the records, are not reading, and never go to AI. The server keeps them.
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox } from "../../foundation/ui";
import { casePath } from "./data";
import { PencilIcon } from "./icons";
import { w } from "./words";

type Note = Schemas["MyNote"];

export function MyNotes({
  caseId,
  pageNo,
  trailing,
}: {
  caseId: string;
  pageNo: number;
  /** Shown at the end of the same quiet line (the link to the pages we couldn't read). */
  trailing?: ReactNode;
}) {
  const loaded = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/my-notes", casePath(caseId))),
    [caseId],
  );
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{
    tone: "ok" | "error";
    text: string;
  } | null>(null);

  useEffect(() => {
    if (!loaded.data) return;
    setNotes(loaded.data.notes);
    setDraft(loaded.data.notes.find((n) => n.page_no === pageNo)?.note ?? "");
  }, [loaded.data, pageNo]);

  const mine = notes?.find((n) => n.page_no === pageNo) ?? null;
  const bookmarked = mine?.bookmarked ?? false;

  async function keep(
    next: { bookmarked: boolean; note: string },
    savedNote: boolean,
  ) {
    if (busy) return;
    setBusy(true);
    setSaid(null);
    try {
      const out = await unwrap(
        api.PUT("/v1/cases/{case_id}/pages/{page_no}/my-note", {
          params: { path: { case_id: caseId, page_no: pageNo } },
          body: next,
        }),
      );
      setNotes((list) => {
        const rest = (list ?? []).filter((n) => n.page_no !== pageNo);
        return out.bookmarked || out.note
          ? [...rest, out].sort((a, b) => a.page_no - b.page_no)
          : rest;
      });
      if (savedNote) setSaid({ tone: "ok", text: w("read.notes.saved") });
    } catch (error) {
      setSaid({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  if (!notes) return trailing ? <div className="rd-notes"><div className="rd-notes__line">{trailing}</div></div> : null;
  return (
    <section className="rd-notes" aria-labelledby="rd-notes-head">
      <h3 id="rd-notes-head" className="rd-sr">{w("read.notes.heading")}</h3>
      <div className="rd-notes__line">
        <Checkbox
          label={w("read.notes.bookmark")}
          checked={bookmarked}
          disabled={busy}
          onChange={(e) =>
            void keep(
              { bookmarked: e.target.checked, note: mine?.note ?? "" },
              false,
            )
          }
        />
        <Button
          variant="quiet"
          className="rd-notes__add"
          aria-expanded={shown}
          aria-controls="rd-notes-body"
          onClick={() => setShown((o) => !o)}
        >
          <PencilIcon />
          {mine?.note ? w("read.notes.note") : w("read.notes.add")}
        </Button>
        {trailing}
      </div>
      {shown && (
        <div id="rd-notes-body" className="rd-notes__body">
          <p className="rd-note">{w("read.notes.private")}</p>
          <label htmlFor="rd-my-note" className="rd-label">
            {w("read.notes.note")}
          </label>
          <textarea
            id="rd-my-note"
            className="av-input rd-notes__text"
            rows={3}
            maxLength={2000}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setSaid(null);
            }}
          />
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => void keep({ bookmarked, note: draft }, true)}
          >
            {w("read.notes.save")}
          </Button>
          {said && (
            <p
              className={
                said.tone === "ok" ? "rd-notes__said" : "rd-goto__error"
              }
              role={said.tone === "ok" ? "status" : "alert"}
            >
              {said.text}
            </p>
          )}
          {notes.length > 0 && (
            <>
              <Button
                variant="secondary"
                aria-expanded={open}
                aria-controls="rd-notes-list"
                onClick={() => setOpen((o) => !o)}
              >
                {w("read.notes.list_button", { count: notes.length })}
              </Button>
              {open && (
                <ul
                  id="rd-notes-list"
                  className="rd-notes__list"
                  aria-label={w("read.notes.list")}
                >
                  {notes.map((n) => (
                    <li key={n.page_no}>
                      <Link to={`/cases/${caseId}/read/${n.page_no}`}>
                        {w("read.page_alt", { page: n.page_no })}
                      </Link>
                      <span className="rd-notes__what">
                        {n.note || w("read.notes.bookmarked")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

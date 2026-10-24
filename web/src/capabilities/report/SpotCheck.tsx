// The colleague's spot-check page (docs/security/threat-model.md T9): no sign-in. The emailed code opens the link,
// the colleague sees small watermarked pictures of the cited words and says whether each value matches. Every
// outcome is a 200 with a status; the server counts wrong codes and locks the link.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Notice, TextField, useBusy } from "../../foundation/ui";
import { w } from "./words";

type Item = Schemas["ColleagueItem"];
type Status = Schemas["ColleagueView"]["status"] | Schemas["ColleagueReplyOut"]["status"];
const PROBLEMS = ["bad_code", "locked", "expired", "unknown", "invalid"] as const;
const CLOSED: Status[] = ["locked", "expired", "unknown"];
const ERRORS = { too_many_attempts: "report.colleague.too_many", network_error: "report.colleague.network" };

export function SpotCheckScreen() {
  const { token = "" } = useParams();
  const [code, setCode] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const [done, setDone] = useState(false);
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const { busy: opening, run: guard } = useBusy();

  const attempt = async (work: () => Promise<Status>) => {
    setProblem(null);
    setClosed(false);
    try {
      const status = await work();
      const found = PROBLEMS.find((p) => p === status);
      if (found) setProblem(w(`colleague.${found}`));
      setClosed(CLOSED.includes(status));
    } catch (err) {
      setProblem(errorMessage(err, ERRORS));
    }
  };
  const open = () =>
    attempt(async () => {
      const view = await unwrap(api.GET("/v1/spot-checks/{token}", { params: { path: { token }, header: { "X-Spot-Check-Code": code } } }));
      if (view.status === "ok") {
        setItems(view.items);
        setDone(view.responded);
      }
      return view.status;
    });
  const send = () =>
    attempt(async () => {
      const body = { code, answers: (items ?? []).map((i) => (answers[i.fact_id] ? { fact_id: i.fact_id, agrees: true } : { fact_id: i.fact_id, agrees: false, reason: "does_not_match" })) };
      const reply = await unwrap(api.POST("/v1/spot-checks/{token}/responses", { params: { path: { token } }, body }));
      if (reply.status === "ok" || reply.status === "already") setDone(true);
      return reply.status;
    });

  return (
    <main className="av-page av-stack">
      <h1>{w("colleague.title")}</h1>
      {problem && <Notice tone="error"><p>{problem}</p></Notice>}
      {done ? (
        <Notice tone="success"><p>{w("colleague.thanks")}</p></Notice>
      ) : items === null ? (
        !closed && (
          <form className="av-stack" onSubmit={(e) => { e.preventDefault(); void guard(open); }}>
            <p>{w("colleague.intro")}</p>
            <TextField label={w("colleague.code")} name="code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.trim())} />
            <Button type="submit" busy={opening} disabled={!/^\d{6}$/.test(code)}>{w("colleague.open")}</Button>
          </form>
        )
      ) : (
        <div className="av-stack">
          <p>{w("colleague.ask")}</p>
          {items.map((item) => (
            <fieldset key={item.fact_id} className="av-card av-stack av-report-fieldset">
              <legend className="av-report-words">{item.value}</legend>
              {item.crop && <img src={`data:image/png;base64,${item.crop}`} alt={w("colleague.crop")} className="av-report-crop" />}
              {([true, false] as const).map((agrees) => (
                <label key={String(agrees)} className="av-report-radio">
                  <input type="radio" name={item.fact_id} checked={answers[item.fact_id] === agrees} onChange={() => setAnswers({ ...answers, [item.fact_id]: agrees })} />
                  <span>{w(agrees ? "colleague.agree" : "colleague.differ")}</span>
                </label>
              ))}
            </fieldset>
          ))}
          <Button onClick={() => send()} disabled={items.some((i) => answers[i.fact_id] === undefined)}>{w("colleague.send")}</Button>
        </div>
      )}
    </main>
  );
}


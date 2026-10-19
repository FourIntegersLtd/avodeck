import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { t } from "../i18n";
import { websocketUrl } from "../platform-client";
import { Button } from "./Button";

export interface DictationBoxProps {
  caseId: string;
  /** What the words are for (the API passes it on to the speech service's phrase list). */
  context: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  /** The framed form of the canvas: the box with a footer holding "Your words only" and Dictate. */
  bar?: boolean;
  /** Small words beside the bar, such as "Saved as you go". */
  note?: ReactNode;
}

type State = "idle" | "starting" | "listening" | "finishing";
type Problem = "unavailable" | "microphone" | null;

/**
 * A text box the expert can type in or dictate into. Audio goes only to the API's WebSocket
 * (rule 4); the words that come back are added to the end of the box for the expert to edit.
 */
export function DictationBox({ caseId, context, label, value, onChange, rows = 8, bar, note }: DictationBoxProps) {
  const id = useId();
  const [state, setState] = useState<State>("idle");
  const [problem, setProblem] = useState<Problem>(null);
  const text = useRef(value);
  const socket = useRef<WebSocket | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  text.current = value;

  const release = () => {
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    recorder.current = null;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  };

  const finish = (next: Problem) => {
    release();
    const ws = socket.current;
    socket.current = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      if (ws.readyState < 2) ws.close();
    }
    setProblem(next);
    setState("idle");
  };

  useEffect(() => () => finish(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setProblem(null);
    setState("starting");
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("no microphone");
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      finish("microphone");
      return;
    }
    const ws = new WebSocket(websocketUrl(`/v1/cases/${encodeURIComponent(caseId)}/dictation?context=${encodeURIComponent(context)}`));
    socket.current = ws;
    ws.onopen = () => {
      const rec = new MediaRecorder(stream.current!);
      recorder.current = rec;
      rec.ondataavailable = (e) => {
        if (e.data.size > 0 && ws.readyState === 1) ws.send(e.data);
      };
      rec.start(250);
      setState("listening");
    };
    ws.onmessage = (e: MessageEvent) => {
      let message: { type?: string; text?: string };
      try {
        message = JSON.parse(String(e.data)) as { type?: string; text?: string };
      } catch {
        return;
      }
      if (message.type === "segment" && message.text) {
        const joined = text.current.trim() ? `${text.current.replace(/\s+$/, "")} ${message.text}` : message.text;
        text.current = joined;
        onChange(joined);
      } else if (message.type === "done") {
        finish(null);
      } else if (message.type === "error") {
        finish("unavailable");
      }
    };
    ws.onerror = () => finish("unavailable");
    ws.onclose = () => finish("unavailable");
  };

  const stop = () => {
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    if (socket.current?.readyState === 1) socket.current.send(JSON.stringify({ type: "end" }));
    setState("finishing");
  };

  const busy = state !== "idle";
  const button =
    state === "listening" ? (
      <Button variant="secondary" onClick={stop}>
        {t("ui.dictation.stop")}
      </Button>
    ) : (
      <Button variant="secondary" onClick={() => void start()} disabled={busy}>
        {t("ui.dictation.start")}
      </Button>
    );
  const live = (
    <p className="av-dictation__live" role="status">
      {state === "listening" ? t("ui.dictation.listening") : state === "finishing" ? t("ui.dictation.finishing") : ""}
    </p>
  );
  const problemText = problem && (
    <p className="av-field__error" role="alert">
      {t(problem === "microphone" ? "ui.dictation.microphone" : "ui.dictation.unavailable")}
    </p>
  );
  return (
    <div className="av-field av-dictation">
      <label className="av-field__label" htmlFor={id}>
        {label}
      </label>
      {bar ? (
        <div className="av-dictation__frame">
          <textarea id={id} className="av-textarea av-dictation__text" rows={rows} value={value} onChange={(e) => onChange(e.target.value)} />
          <div className="av-dictation__footer">
            <span className="av-dictation__own">{t("ui.dictation.own_words")}</span>
            {live}
            {note && <span className="av-dictation__note">{note}</span>}
            {button}
          </div>
        </div>
      ) : (
        <>
          <textarea id={id} className="av-textarea" rows={rows} value={value} onChange={(e) => onChange(e.target.value)} />
          <div className="av-dictation__bar">
            {button}
            {live}
            {note && <span className="av-dictation__note">{note}</span>}
          </div>
        </>
      )}
      {problemText}
    </div>
  );
}

/** The dictation box in the canvas's framed form ("Your words only · Dictate"). */
export function DictationBar(props: Omit<DictationBoxProps, "bar">) {
  return <DictationBox {...props} bar />;
}

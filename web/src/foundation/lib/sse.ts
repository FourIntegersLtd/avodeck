// Shared server-sent-events hook: opens an EventSource against `url` when the browser has one,
// otherwise falls back to polling `pollFallback` on a `window.setInterval`. Either way it cleans up
// on unmount or when `url` changes, so a screen just swaps its own EventSource/polling branch for
// one call. `null` means "nothing to follow yet" and opens neither.
import { useEffect } from "react";

export function useEventStream<T>(
  url: string | null,
  onUpdate: (data: T) => void,
  pollFallback: () => Promise<T>,
  pollMs: number,
): void {
  useEffect(() => {
    if (!url) return;
    if (typeof EventSource !== "undefined") {
      const events = new EventSource(url, { withCredentials: true });
      events.onmessage = (e: MessageEvent<string>) => onUpdate(JSON.parse(e.data) as T);
      return () => events.close();
    }
    let live = true;
    const look = () => pollFallback().then((data) => live && onUpdate(data), () => undefined);
    void look();
    const timer = window.setInterval(look, pollMs);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);
}

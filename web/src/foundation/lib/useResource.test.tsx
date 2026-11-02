// Pages seen before show at once (stale-while-revalidate), and the cache can never show the wrong thing:
// only successes are kept (never a 423 blind-read refusal), anything that changes data or who is signed
// in empties it, reload always fetches fresh, and it lives in memory only.
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { apiError, http, HttpResponse, json, server } from "../../test/api";
import { api, ApiError, unwrap } from "../platform-client";
import { claimResourceCache, clearResourceCache } from "./resourceCache";
import { useResource, type Resource } from "./useResource";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let latest: Resource<string> | undefined;

function Probe({ load, k = "a", cache }: { load: () => Promise<string>; k?: unknown; cache?: string }) {
  const found = useResource(load, [k], cache ? { cache } : undefined);
  latest = found;
  const text = found.loading ? "loading" : found.error ? `error ${found.error.status}` : `data ${found.data}`;
  return <p data-testid="out">{text}</p>;
}

const out = () => screen.getByTestId("out").textContent;

async function loadOnce(cache: string, value: string, k: unknown = "a") {
  const view = render(<Probe load={() => Promise.resolve(value)} k={k} cache={cache} />);
  await waitFor(() => expect(out()).toBe(`data ${value}`));
  view.unmount();
}

afterEach(() => {
  clearResourceCache();
  latest = undefined;
});

describe("useResource with a cache name", () => {
  test("a second mount within 30 seconds shows the kept answer and makes no new fetch", async () => {
    await loadOnce("probe.instant", "old");
    const load = vi.fn(() => Promise.resolve("new"));
    render(<Probe load={load} cache="probe.instant" />);
    expect(out()).toBe("data old"); // first render, no loading state
    await act(async () => {});
    expect(load).not.toHaveBeenCalled();
  });

  test("a change to case A does not drop case B, and does drop Today", async () => {
    server.use(http.post("/v1/cases/case-a/notes", () => new HttpResponse(null, { status: 204 })));
    await loadOnce("probe.case", "a", "case-a");
    await loadOnce("probe.case", "b", "case-b");
    await loadOnce("probe.today", "today");
    await unwrap(api.POST("/v1/cases/{case_id}/notes" as never, { params: { path: { case_id: "case-a" } } } as never));
    render(<Probe load={() => new Promise<string>(() => {})} k="case-b" cache="probe.case" />);
    expect(out()).toBe("data b");
    render(<Probe load={() => new Promise<string>(() => {})} k="case-a" cache="probe.case" />);
    expect(screen.getAllByTestId("out")[1]!.textContent).toBe("data a"); // marked stale, still shown while it is fetched again
    const stale = vi.fn(() => Promise.resolve("fresh"));
    render(<Probe load={stale} cache="probe.today" />);
    await waitFor(() => expect(stale).toHaveBeenCalled());
  });

  test("a failed background reload keeps the data already shown", async () => {
    await loadOnce("probe.keep", "shown");
    const load = vi.fn(() => Promise.reject<string>(new ApiError(500, "server_error")));
    render(<Probe load={load} cache="probe.keep" />);
    expect(out()).toBe("data shown");
    act(() => latest!.reload());
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2), { timeout: 4000 }); // one try and one more
    await act(async () => {});
    expect(out()).toBe("data shown");
  });

  test("a change to a case drops a read of that case that was already on its way", async () => {
    server.use(http.post("/v1/cases/case-a/notes", () => new HttpResponse(null, { status: 204 })));
    await loadOnce("probe.inflight", "old", "case-a");
    const slow = deferred<string>();
    const first = render(<Probe load={() => slow.promise} k="case-a" cache="probe.inflight" />);
    act(() => latest!.reload());
    await unwrap(api.POST("/v1/cases/{case_id}/notes" as never, { params: { path: { case_id: "case-a" } } } as never));
    await act(async () => slow.resolve("from before the change"));
    first.unmount();
    const load = vi.fn(() => Promise.resolve("after"));
    render(<Probe load={load} k="case-a" cache="probe.inflight" />);
    await waitFor(() => expect(load).toHaveBeenCalled());
  });

  test("a change to a case also refreshes balance and messages, which carry no case id", async () => {
    server.use(http.post("/v1/cases/case-a/notes", () => new HttpResponse(null, { status: 204 })));
    await loadOnce("setup/shared#Balance:found", "b");
    await loadOnce("account/Messages#Messages:found", "m");
    await loadOnce("foundation/lib/messages#MessagesProvider:found", "p");
    await unwrap(api.POST("/v1/cases/{case_id}/notes" as never, { params: { path: { case_id: "case-a" } } } as never));
    for (const name of ["setup/shared#Balance:found", "account/Messages#Messages:found", "foundation/lib/messages#MessagesProvider:found"]) {
      const load = vi.fn(() => Promise.resolve("fresh"));
      const view = render(<Probe load={load} cache={name} />);
      await waitFor(() => expect(load).toHaveBeenCalled());
      view.unmount();
    }
  });

  test("different arguments are different entries", async () => {
    await loadOnce("probe.args", "page one", 1);
    const fresh = deferred<string>();
    render(<Probe load={() => fresh.promise} k={2} cache="probe.args" />);
    expect(out()).toBe("loading");
  });

  test("a call without a cache name never shows a kept answer", async () => {
    const first = render(<Probe load={() => Promise.resolve("old")} />);
    await waitFor(() => expect(out()).toBe("data old"));
    first.unmount();
    render(<Probe load={() => new Promise<string>(() => {})} />);
    expect(out()).toBe("loading");
  });

  test("arguments that are not plain values are not cached", async () => {
    await loadOnce("probe.object", "old", { page: 1 });
    render(<Probe load={() => new Promise<string>(() => {})} k={{ page: 1 }} cache="probe.object" />);
    expect(out()).toBe("loading");
  });

  test("an error is never kept: a 423 blind-read refusal leaves nothing to show before the page is read", async () => {
    const refused = render(<Probe load={() => Promise.reject(new ApiError(423, "blind_read"))} cache="probe.facts" />);
    await waitFor(() => expect(out()).toBe("error 423"));
    refused.unmount();
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.facts" />);
    expect(out()).toBe("loading");
  });

  test("a kept answer is dropped when a fresh load is refused", async () => {
    await loadOnce("probe.dropped", "facts");
    let refuse = false;
    const again = render(
      <Probe load={() => (refuse ? Promise.reject(new ApiError(423, "blind_read")) : Promise.resolve("facts"))} cache="probe.dropped" />,
    );
    expect(out()).toBe("data facts");
    refuse = true;
    act(() => latest!.reload());
    await waitFor(() => expect(out()).toBe("error 423"));
    again.unmount();
  });

  test("reload always asks the server and never brings back a kept answer", async () => {
    await loadOnce("probe.reload", "old");
    let n = 0;
    const load = vi.fn(() => Promise.resolve(`fresh ${++n}`));
    render(<Probe load={load} cache="probe.reload" />);
    await act(async () => {});
    expect(out()).toBe("data old");
    act(() => latest!.reload());
    await waitFor(() => expect(out()).toBe("data fresh 1"));
    expect(load).toHaveBeenCalledTimes(1);
  });

  test("a change (POST, PUT, PATCH or DELETE) empties the cache", async () => {
    server.use(http.post("/v1/me/sessions/revoke-all", () => new HttpResponse(null, { status: 204 })));
    await loadOnce("probe.mutation", "old");
    await unwrap(api.POST("/v1/me/sessions/revoke-all"));
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.mutation" />);
    expect(out()).toBe("loading");
  });

  test("an answer that was on its way during a change is not kept", async () => {
    server.use(http.post("/v1/me/sessions/revoke-all", () => new HttpResponse(null, { status: 204 })));
    const slow = deferred<string>();
    const first = render(<Probe load={() => slow.promise} cache="probe.race" />);
    await unwrap(api.POST("/v1/me/sessions/revoke-all"));
    await act(async () => slow.resolve("from before the change"));
    first.unmount();
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.race" />);
    expect(out()).toBe("loading");
  });

  test("signing out empties the cache", async () => {
    server.use(http.post("/v1/auth/sign-out", () => new HttpResponse(null, { status: 204 })));
    await loadOnce("probe.signout", "old");
    await unwrap(api.POST("/v1/auth/sign-out"));
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.signout" />);
    expect(out()).toBe("loading");
  });

  test("a session that has ended empties the cache", async () => {
    server.use(http.get("/v1/today", () => apiError(401, "session_expired")));
    await loadOnce("probe.expired", "old");
    await unwrap(api.GET("/v1/today")).catch(() => undefined);
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.expired" />);
    expect(out()).toBe("loading");
  });

  test("a different person signed in empties the cache", async () => {
    claimResourceCache("person-a");
    await loadOnce("probe.person", "a's answer");
    claimResourceCache("person-a");
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.person" />).unmount();
    claimResourceCache("person-b");
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.person" />);
    expect(out()).toBe("loading");
  });

  test("a read (GET) leaves the cache alone", async () => {
    server.use(http.get("/v1/cell", () => json({ storage_place: null })));
    await loadOnce("probe.read", "old");
    await unwrap(api.GET("/v1/cell"));
    render(<Probe load={() => new Promise<string>(() => {})} cache="probe.read" />);
    expect(out()).toBe("data old");
  });
});

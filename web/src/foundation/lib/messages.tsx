// The signed-in person's messages summary: the bell's unread count and the bounce banner (F33).
// Screens that change messages call `reload()` so the bell stays right.
import { createContext, useContext, type ReactNode } from "react";
import { api, unwrap } from "../platform-client";
import { useResource } from "./useResource";

export interface MessagesSummary {
  unread: number;
  bounced: boolean;
  reload: () => void;
}

const MessagesContext = createContext<MessagesSummary | null>(null);

export function MessagesProvider({ children }: { children: ReactNode }) {
  const found = useResource(() => unwrap(api.GET("/v1/notifications", { params: { query: { unread: true } } })), [], { cache: "foundation/lib/messages#MessagesProvider:found" });
  const value = { unread: found.data?.unread_count ?? 0, bounced: found.data?.email_bounced ?? false, reload: found.reload };
  return <MessagesContext.Provider value={value}>{children}</MessagesContext.Provider>;
}

/** The summary, or null outside the signed-in part of the app. */
export function useMessages(): MessagesSummary | null {
  return useContext(MessagesContext);
}

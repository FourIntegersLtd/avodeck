import { createContext, useContext } from "react";
import type { Schemas } from "../platform-client";

export interface Session {
  me: Schemas["Me"];
  status: Schemas["SessionStatus"];
  /** Avodeck support staff: the support frame and console instead of an expert's places. */
  isSupport: boolean;
}

export const SessionContext = createContext<Session | null>(null);

/** The signed-in person (inside the signed-in part of the app only). */
export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside a signed-in screen");
  return session;
}

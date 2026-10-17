import type { ReactNode } from "react";
import { StepUpProvider } from "../foundation/lib/stepUp";

/** Everything every screen needs around it. */
export function AppProviders({ children }: { children: ReactNode }) {
  return <StepUpProvider>{children}</StepUpProvider>;
}

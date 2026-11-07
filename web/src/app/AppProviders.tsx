import type { ReactNode } from "react";
import { ConfirmProvider } from "../foundation/lib/confirm";
import { StepUpProvider } from "../foundation/lib/stepUp";

/** Everything every screen needs around it. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <StepUpProvider>
      <ConfirmProvider>{children}</ConfirmProvider>
    </StepUpProvider>
  );
}

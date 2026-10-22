import { ClientApp } from "./ClientApp";

// Until each capability has its own Next route, one catch-all hands every address to the existing
// client-side router, so screens move across one at a time without a big switch-over.
export default function Page() {
  return <ClientApp />;
}

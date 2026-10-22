// A question's chip on S11 (card, ribbon pin, side panel, list). One place, so the highlight lives here.
import { QChip } from "../../foundation/ui";

export function QuestionChip({ questionId, n }: { questionId: string; n: number }) {
  void questionId;
  return <QChip n={n} />;
}

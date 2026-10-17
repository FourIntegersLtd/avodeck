"""Make slice C's recorded OCR and model responses.

Run from `api/`: `poetry run python tests/fixtures/setup/record_setup.py`.

Nothing here calls a real service. OCR is stood in by the PDF's own text layer (words `p{page}w{n}`, one
line per text line). The T-06 answers are the honest ones a careful model would give: for each question and
assumption in `evals/.../expected/questions.json` (and the supplementary letter's new question), the run of
word ids whose words are exactly those words, copied as the model sees them (identifiers already
replaced). Requests are built exactly as `AiKit.run` builds them, so the recordings replay in tests.

`recordings-paraphrased/` holds one answer whose first question is reworded: the server must refuse it.
Re-run this after a change to the T-06 prompt, its schema or the launch packs' prompt context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parents[2]
REPO = API.parent
sys.path.insert(0, str(API))

from app.core.ai import pii  # noqa: E402
from app.core.ai.bedrock import ModelRequest, ModelResponse, RecordedBedrock  # noqa: E402
from app.core.ai.cell import model_for  # noqa: E402
from app.core.ai.ocr import RecordedOcr, _result  # noqa: E402
from app.core.ai.recordings import FixtureStore  # noqa: E402
from app.core.packs import default_library  # noqa: E402
from app.features.setup import ai_tasks  # noqa: E402
from app.features.setup.letter import Letter  # noqa: E402
from tests.support.ai import cell  # noqa: E402

EXPECTED = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"
LETTER = HERE / "letter-of-instruction.pdf"
SUPPLEMENTARY = HERE / "supplementary-letter-of-instruction.pdf"
ROOT = HERE / "recordings"
PARAPHRASED = HERE / "recordings-paraphrased"
NAMES = ["Margaret Ashworth"]
PARAPHRASE = "Please give your opinion on whether the care given in 2024 was good enough."


def _supplementary() -> None:
    if SUPPLEMENTARY.exists():
        return
    sys.path.insert(0, str(REPO / "evals" / "generator"))
    import tempfile

    from inputs import supplementary_instructions  # type: ignore[import-not-found]

    with tempfile.TemporaryDirectory() as tmp:
        supplementary_instructions(tmp)
        (Path(tmp) / SUPPLEMENTARY.name).replace(SUPPLEMENTARY)


def ocr_pages(pdf: Path) -> dict[str, object]:
    from pypdf import PdfReader

    pages = []
    for p, page in enumerate(PdfReader(str(pdf)).pages, 1):
        words = []
        for line_no, line in enumerate((page.extract_text() or "").splitlines()):
            top = 0.4 + line_no * 0.2
            for x, text in enumerate(line.split()):
                left = 0.5 + x * 0.3
                words.append(
                    {
                        "id": f"p{p}w{len(words) + 1}",
                        "text": text,
                        "polygon": [left, top, left + 0.25, top, left + 0.25, top + 0.12, left, top + 0.12],
                        "confidence": 0.99,
                        "handwritten": False,
                    }
                )
        pages.append({"number": p, "width": 8.27, "height": 11.69, "words": words})
    return {"pages": pages}


def find_run(letter: Letter, text: str) -> list[str]:
    target = text.split()
    words = [w.text for w in letter.words]
    for i in range(len(words) - len(target) + 1):
        if words[i : i + len(target)] == target:
            return [w.id for w in letter.words[i : i + len(target)]]
    raise SystemExit("an expected passage is not in the letter's words")


def item(letter: Letter, text: str, para_ref: str, kind: str | None = None) -> dict[str, object]:
    ids = find_run(letter, text)
    out: dict[str, object] = {
        "text_verbatim": " ".join(letter.masked[i] for i in ids),
        "word_ids": ids,
        "para_ref": para_ref,
        "reason": "numbered question" if kind else "stated assumption",
    }
    if kind:
        out["kind"] = kind
    return out


def main() -> None:
    _supplementary()
    settings = cell()
    pack = default_library().combine("gb-eaw", "clin-neg-breach", "general-practice")
    task = ai_tasks.t06(pack)
    expected = json.loads((EXPECTED / "questions.json").read_text())
    supp = json.loads((EXPECTED / "inputs.json").read_text())["instruction_version_2"]
    kinds = {q["text_verbatim"]: q["kind"] for q in expected["questions"]}

    for root in (ROOT, PARAPHRASED):
        for old in root.glob("*/*.json"):
            old.unlink()
    store, paraphrased_store = FixtureStore(ROOT), FixtureStore(PARAPHRASED)
    model = RecordedBedrock(settings, store)
    paraphrased_model = RecordedBedrock(settings, paraphrased_store)

    for name, pdf in (("letter", LETTER), ("supplementary", SUPPLEMENTARY)):
        document = pdf.read_bytes()
        response = ocr_pages(pdf)
        store.record(RecordedOcr.kind, RecordedOcr.request(document, "layout"), response, f"{name}-ocr")
        paraphrased_store.record(RecordedOcr.kind, RecordedOcr.request(document, "layout"), response, f"{name}-ocr")
        letter = Letter.from_ocr(_result("layout", response))
        letter.mask(pack=pack, names=NAMES, dates_of_birth=())
        if name == "letter":
            questions = [item(letter, q["text_verbatim"], q["para_ref"], q["kind"]) for q in expected["questions"]]
            assumptions = [item(letter, a["text_verbatim"], a["para_ref"]) for a in expected["assumptions"]]
        else:
            # The supplementary letter asks one new question; the model also finds nothing else.
            questions = [
                item(letter, supp["new_question"], supp["para_ref"], kinds[expected["questions"][0]["text_verbatim"]])
            ]
            assumptions = []
        request = ModelRequest(
            model_id=model_for(settings, task.model),
            system=task.system,
            context=pack.prompt_context(task.task_id),
            text=pii.strip(letter.prompt_text(), pack=pack, names=NAMES, dates_of_birth=()).text,
            schema=task.schema,
        )
        output = {"questions": questions, "assumptions": assumptions}
        model.record(request, ModelResponse(output=output, input_tokens=2400, output_tokens=600), f"{name}-t06")
        if name == "letter":
            reworded = {**output, "questions": [{**questions[0], "text_verbatim": PARAPHRASE}, *questions[1:]]}
            paraphrased_model.record(
                request, ModelResponse(output=reworded, input_tokens=2400, output_tokens=600), f"{name}-t06"
            )
    print("recorded")  # noqa: T201


if __name__ == "__main__":
    main()

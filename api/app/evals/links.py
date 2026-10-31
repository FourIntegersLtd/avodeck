"""T-11 links evaluation: for each question of the synthetic bundle, how many of the expected facts
are among the first k offered (recall@k). Embeds the bundle's facts as the pipeline does (identifiers
stripped, `kind value`), ranks by cosine, optionally reranks. Prints counts only (rule 6).

`FLOOR` is the T-11 recall@10 floor recorded in `docs/decisions/deferred.md` (ADR 0029).
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Hashable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from app.core.ai import pii
from app.core.ai.embed import Embedder, embed_queries
from app.core.packs import default_library
from app.features.understand.links import IdentityReranker, Reranker, candidates

FLOOR = 0.5
K = 10

Key = tuple[int, str, str]


def _text(fact: dict) -> str:  # type: ignore[type-arg]
    """The wording embedded for a fact: its kind, value and day, as `understand/jobs.py` does."""
    day = datetime.date.fromisoformat(fact["event_date"])
    return f"{fact['kind']} {fact['value']} {day.day} {day.strftime('%B %Y')}"


@dataclass(frozen=True)
class LinksResult:
    per_question: list[float]

    @property
    def recall(self) -> float:
        return sum(self.per_question) / len(self.per_question)


def recall_at_k(ranked: Sequence[Hashable], expected: AbstractSet[Hashable], k: int) -> float:
    return len(set(ranked[:k]) & expected) / len(expected) if expected else 0.0


def evaluate(embedder: Embedder, expected_dir: Path, *, reranker: Reranker | None = None, k: int = K) -> LinksResult:
    """`expected_dir` is `evals/<jurisdiction>/<case type>/<specialty>/expected`."""
    codes = (expected_dir.parents[2].name, expected_dir.parents[1].name, expected_dir.parent.name)
    pack = default_library().combine(*codes)
    case = json.loads((expected_dir / "case.json").read_text())

    def stripped(text: str) -> pii.Stripped:
        return pii.strip(text, pack=pack, names=case["case_names"])

    facts: dict[Key, str] = {}
    for fact in json.loads((expected_dir / "facts.json").read_text()):
        if not case["period_start"] <= (fact["event_date"] or "") <= case["period_end"]:
            continue  # the pool is the facts of the period under review, as the expert's case scope sets it
        facts.setdefault((fact["page"], fact["kind"], fact["quote"]), _text(fact))
    keys = list(facts)
    ids = [uuid.UUID(int=n + 1) for n in range(len(keys))]
    key_of = dict(zip(ids, keys, strict=True))
    vectors = embedder.embed([stripped(facts[key]) for key in keys])
    pool = [(fid, vector, facts[key]) for fid, key, vector in zip(ids, keys, vectors, strict=True)]

    named = {
        q["number"]: q["text_verbatim"] for q in json.loads((expected_dir / "questions.json").read_text())["questions"]
    }
    wanted = json.loads((expected_dir / "links.json").read_text())["questions"]
    texts = [stripped(q.get("text") or named[q["number"]]) for q in wanted]
    scores = []
    for text, vector, question in zip(texts, embed_queries(embedder, texts), wanted, strict=True):
        offered = candidates((vector, text.text), pool, k=k, reranker=reranker or IdentityReranker())
        expected = {(f["page"], f["kind"], f["quote"]) for f in question["expected"]}
        scores.append(recall_at_k([key_of[fid] for fid in offered], expected, k))
    return LinksResult(scores)

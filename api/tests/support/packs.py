"""The two pack combinations every core test runs under, and raw pack files to compare against.

Expected words are read from the pack files themselves, never written into tests, so a test
proves the value came from the pack (rule 11).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
LAUNCH_ROOT = REPO / "packs"
FIXTURE_ROOT = LAUNCH_ROOT / "_fixtures"


@dataclass(frozen=True)
class Combo:
    jurisdiction: str
    case_type: str
    specialty: str
    root: Path

    @property
    def codes(self) -> tuple[str, str, str]:
        return (self.jurisdiction, self.case_type, self.specialty)

    def raw(self, layer: str, name: str) -> Any:
        """A pack file as written (`layer` is jurisdiction, case-type, specialty or overlay)."""
        code = {
            "jurisdiction": self.jurisdiction,
            "case-type": self.case_type,
            "specialty": self.specialty,
            "overlay": f"{self.jurisdiction}/{self.specialty}",
        }[layer]
        path = self.root / layer / code / name
        if not path.exists():
            return None
        return yaml.safe_load(path.read_text()) if path.suffix == ".yaml" else path.read_text()

    def word(self, key: str) -> str:
        """A vocabulary word after the merge: overlay over specialty over jurisdiction."""
        for layer in ("overlay", "specialty", "jurisdiction"):
            vocab = self.raw(layer, "vocabulary.yaml") or {}
            if key in vocab:
                return str(vocab[key])
        raise KeyError(key)

    def case_row(self) -> dict[str, str]:
        roles = self.raw("case-type", "pack.yaml")["expert_roles"]
        return {
            "jurisdiction": self.jurisdiction,
            "case_type": self.case_type,
            "specialty": self.specialty,
            "expert_role": roles[0],
        }


LAUNCH = Combo("gb-eaw", "clin-neg-breach", "general-practice", LAUNCH_ROOT)
FIXTURE = Combo("zz-test", "zz-case", "zz-specialty", FIXTURE_ROOT)
BOTH = [LAUNCH, FIXTURE]


def copy_packs(dest: Path) -> tuple[Path, Path]:
    """A writable copy of packs/ (with schema/ and _fixtures/); returns (launch root, fixture root)."""
    root = dest / "packs"
    shutil.copytree(LAUNCH_ROOT, root)
    return root, root / "_fixtures"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

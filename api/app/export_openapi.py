"""Print the API's OpenAPI document: `python -m app.export_openapi > ../openapi.json` (`make api-client`).

With `--pack-fixtures` it prints the pack data the web tests run screen copy under instead: the launch
packs and the fixture packs (`packs/_fixtures`), as `GET /v1/jurisdictions/{code}` and
`GET /v1/cases/{id}/pack` return them (rule 11).

Keys are sorted and indented so the committed file only changes when the API does.
"""

from __future__ import annotations

import json
import sys

from app.core.packs import REPO, load_library
from app.core.routes.packs import JurisdictionSummary, case_pack
from app.main import create_app

PACK_SETS = {
    "launch": ("gb-eaw", "clin-neg-breach", "general-practice"),
    "fixture": ("zz-test", "zz-case", "zz-specialty"),
}


def openapi_json() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def pack_fixtures_json() -> str:
    library = load_library(REPO / "packs", REPO / "packs" / "_fixtures")
    data = {
        name: {
            "jurisdiction": JurisdictionSummary(**library.jurisdiction_summary(codes[0])).model_dump(mode="json"),
            "case_pack": case_pack(library.combine(*codes)).model_dump(mode="json"),
        }
        for name, codes in PACK_SETS.items()
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    sys.stdout.write(pack_fixtures_json() if "--pack-fixtures" in sys.argv[1:] else openapi_json())


if __name__ == "__main__":
    main()

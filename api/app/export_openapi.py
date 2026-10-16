"""Print the API's OpenAPI document: `python -m app.export_openapi > ../openapi.json` (`make api-client`).

Keys are sorted and indented so the committed file only changes when the API does.
"""

from __future__ import annotations

import json
import sys

from app.main import create_app


def openapi_json() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    sys.stdout.write(openapi_json())


if __name__ == "__main__":
    main()

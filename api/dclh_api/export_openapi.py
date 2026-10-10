"""Write the OpenAPI schema to api/openapi.json (input for the generated TypeScript types)."""

from __future__ import annotations

import json
from pathlib import Path

from dclh_api.main import app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "openapi.json"
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()

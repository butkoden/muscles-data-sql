from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SqlGenerationRequest:
    generator_type: str
    name: str
    force: bool = False
    with_tests: bool = False


class SqlResourceGeneratorProvider:
    name = "muscles-data-sql.resource"

    def supports(self, generator_type: str) -> bool:
        return generator_type in {"sql-resource", "resource-sql"}

    def generate(self, project_root: Path, request: SqlGenerationRequest) -> list[str]:
        root = project_root / "generated"
        root.mkdir(exist_ok=True)
        target = root / f"{request.name}.py"
        content = (
            f"class {request.name.title().replace('_', '')}Resource:\n"
            f"    sql_enabled = True\n"
        )
        target.write_text(content, encoding="utf-8")
        return [str(target)]

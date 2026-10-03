"""Run python -m src.operator.contracts.export_schema [output-directory]."""

import argparse
import json
from pathlib import Path

from .primitives import ActionResult, FieldSpec, FillAction, FillReport, SubmissionResult


def export_schemas(output: Path) -> list[Path]:
    """Export deterministic standalone JSON schemas, without candidate data."""
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in (FieldSpec, FillAction, FillReport, ActionResult, SubmissionResult):
        path = output / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path,
                        default=Path(__file__).parent / "schemas")
    export_schemas(parser.parse_args().output)

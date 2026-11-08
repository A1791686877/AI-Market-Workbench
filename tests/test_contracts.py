from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import AiAnalysisResult
from scripts.generate_contracts import main as generate_contracts
from scripts.validate_contracts import main as validate_contracts


ROOT = Path(r"E:\AI-Market-Workbench")


def test_generated_contracts_and_examples_pass_formal_validators() -> None:
    generate_contracts()
    validate_contracts()


def test_case_conflict_and_duplicate_scenarios_are_rejected_by_runtime_model() -> None:
    valid = json.loads((ROOT / "examples" / "valid" / "ai-analysis.json").read_text(encoding="utf-8"))
    wrong_case = json.loads((ROOT / "examples" / "invalid" / "wrong-case-scenario.json").read_text(encoding="utf-8"))
    duplicate = json.loads((ROOT / "examples" / "invalid" / "duplicate-scenario.json").read_text(encoding="utf-8"))
    assert AiAnalysisResult.model_validate(valid)
    with pytest.raises(ValidationError):
        AiAnalysisResult.model_validate(wrong_case)
    with pytest.raises(ValidationError):
        AiAnalysisResult.model_validate(duplicate)


def test_openapi_has_one_generated_source_and_external_ai_schema_ref() -> None:
    document = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    assert document["x-contract-source"] == ["app/models.py", "app/main.py"]
    assert document["x-generated-by"] == "scripts/generate_contracts.py"
    serialized = json.dumps(document)
    assert "./ai-analysis.schema.json" in serialized
    assert set(document["x-contract-source"]) == {"app/models.py", "app/main.py"}


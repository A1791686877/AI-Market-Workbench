from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.readers import read_from_filename
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mcp_contract import build_mcp_contract
from scripts.generate_contracts import build_contracts


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_semantics(instance: dict) -> None:
    scenarios = instance["scenarios"]
    kinds = [item["type"] for item in scenarios]
    if sorted(kinds) != sorted(["BULLISH", "NEUTRAL", "BEARISH"]):
        raise ValueError("scenario set is not exact")
    known_levels = {item["level_id"] for item in instance["key_levels"]}
    used_levels = {item for scenario in scenarios for item in scenario["key_level_ids"]}
    if not used_levels <= known_levels:
        raise ValueError("scenario references unknown key levels")
    subject = instance["subject"]
    if subject["subject_id"] != instance["snapshot"]["subject_id"]:
        raise ValueError("snapshot and result subject mismatch")


def reject_arbitrary_business_objects(node: object, path: str = "$") -> None:
    """Reject untyped object placeholders while allowing schema-valued maps."""
    if isinstance(node, list):
        for index, item in enumerate(node):
            reject_arbitrary_business_objects(item, f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "object":
        if node.get("additionalProperties") is True:
            raise AssertionError(f"arbitrary additionalProperties at {path}")
        structural_keys = {"properties", "allOf", "anyOf", "oneOf", "$ref", "additionalProperties"}
        if not structural_keys.intersection(node):
            raise AssertionError(f"untyped object schema at {path}")
    for key, value in node.items():
        if key == "data" and value == {}:
            raise AssertionError(f"empty data placeholder at {path}.data")
        reject_arbitrary_business_objects(value, f"{path}.{key}")


def main() -> None:
    contracts = ROOT / "contracts"
    openapi_json_path = contracts / "openapi.json"
    openapi_yaml_path = contracts / "openapi.yaml"
    schema_path = contracts / "ai-analysis.schema.json"
    wrapper_path = contracts / "analysis-record.schema.json"

    generated_openapi, generated_schema, generated_wrapper, generated_mcp = build_contracts()
    openapi_json = _load_json(openapi_json_path)
    openapi_yaml = yaml.safe_load(openapi_yaml_path.read_text(encoding="utf-8"))
    if openapi_json != openapi_yaml or openapi_json != generated_openapi:
        raise AssertionError("OpenAPI JSON/YAML/generated source are not deeply identical")
    if _load_json(schema_path) != generated_schema:
        raise AssertionError("AI schema is stale; run generate_contracts.py")
    if _load_json(wrapper_path) != generated_wrapper:
        raise AssertionError("external-reference wrapper is stale")
    if _load_json(contracts / "mcp-tools.json") != generated_mcp:
        raise AssertionError("MCP contract is stale")

    for name, component in openapi_json["components"]["schemas"].items():
        reject_arbitrary_business_objects(component, f"$.components.schemas.{name}")
    reject_arbitrary_business_objects(generated_schema, "$.aiAnalysisSchema")
    for tool in generated_mcp["tools"]:
        reject_arbitrary_business_objects(tool["inputSchema"], f"$.mcp.{tool['name']}.inputSchema")
        reject_arbitrary_business_objects(tool["outputSchema"], f"$.mcp.{tool['name']}.outputSchema")

    # Official OpenAPI validator, both serializations, with relative external refs.
    json_document, json_base_uri = read_from_filename(str(openapi_json_path))
    yaml_document, yaml_base_uri = read_from_filename(str(openapi_yaml_path))
    validate_openapi(json_document, base_uri=json_base_uri)
    validate_openapi(yaml_document, base_uri=yaml_base_uri)

    schema = _load_json(schema_path)
    wrapper = _load_json(wrapper_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(wrapper)
    registry = Registry().with_resource(schema_path.as_uri(), Resource.from_contents(schema))
    wrapper_for_file = dict(wrapper)
    wrapper_for_file["$id"] = wrapper_path.as_uri()
    validator = Draft202012Validator(
        wrapper_for_file,
        registry=registry,
        format_checker=FormatChecker(),
    )

    valid_path = ROOT / "examples" / "valid" / "ai-analysis.json"
    valid = _load_json(valid_path)
    validator.validate(valid)
    validate_semantics(valid)

    invalid_paths = sorted((ROOT / "examples" / "invalid").glob("*.json"))
    if len(invalid_paths) < 3:
        raise AssertionError("at least three invalid examples are required")
    for path in invalid_paths:
        candidate = _load_json(path)
        schema_failed = False
        semantic_failed = False
        try:
            validator.validate(candidate)
        except Exception:
            schema_failed = True
        try:
            validate_semantics(candidate)
        except Exception:
            semantic_failed = True
        if not (schema_failed or semantic_failed):
            raise AssertionError(f"invalid sample unexpectedly passed: {path.name}")

    operation_ids = {
        operation["operationId"]
        for path_item in openapi_json["paths"].values()
        for method, operation in path_item.items()
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }
    mcp = build_mcp_contract()
    for tool in mcp["tools"]:
        Draft202012Validator.check_schema(tool["inputSchema"])
        Draft202012Validator.check_schema(tool["outputSchema"])
        operation_id = tool["annotations"]["restOperationId"]
        if operation_id not in operation_ids:
            raise AssertionError(f"MCP tool refers to missing REST operation: {operation_id}")

    forbidden = {"account", "order", "withdraw", "transfer"}
    route_text = " ".join(openapi_json["paths"]).lower()
    tool_text = " ".join(tool["name"] for tool in mcp["tools"]).lower()
    if any(word in route_text or word in tool_text for word in forbidden):
        raise AssertionError("forbidden trading/account capability leaked into contracts")

    print(
        "PASS: OpenAPI JSON/YAML + external refs; Draft 2020-12 schema; "
        f"1 valid and {len(invalid_paths)} invalid examples; MCP/REST operation mapping."
    )


if __name__ == "__main__":
    main()

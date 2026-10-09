"""Offline design-contract checks. Does not start or test the product."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from nodes(item)


def resolve(root, reference):
    if not reference.startswith("#/"):
        raise ValueError("Only internal references are allowed in this pack")
    value = root
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def main():
    problems = []
    api = read_json(BASE / "contracts" / "openapi.json")
    index = read_json(BASE / "contracts" / "operation-index.json")
    pack = read_json(BASE / "tasks.json")
    fixtures = read_json(BASE / "fixtures" / "acceptance.json")
    block = read_json(BASE / "fixtures" / "block-contract.example.json")
    schemas = api["components"]["schemas"]
    operations = {}
    if api.get("openapi") != "3.1.0" or api.get("x-design-only") is not True:
        problems.append("Missing explicit design-only OpenAPI version")
    for item in nodes(api):
        if "$ref" in item:
            try:
                resolve(api, item["$ref"])
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"Bad reference {item['$ref']}: {exc}")
    for path, item in api["paths"].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            oid = operation["operationId"]
            if oid in operations:
                problems.append(f"Duplicate operationId: {oid}")
            operations[oid] = (method, path, operation)
            expected = set(re.findall(r"\{([^}]+)\}", path))
            actual = {p["name"] for p in operation.get("parameters", []) if p["in"] == "path"}
            if expected != actual:
                problems.append(f"Path parameters mismatch: {oid}")
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "path" and parameter.get("required") is not True:
                    problems.append(f"Optional path parameter: {oid}")
            if not any(str(k).startswith("2") for k in operation.get("responses", {})):
                problems.append(f"No success response: {oid}")
            for security in operation.get("security", api.get("security", [])):
                for name in security:
                    if name not in api["components"]["securitySchemes"]:
                        problems.append(f"Unknown security scheme: {oid}/{name}")
    if {o["operationId"] for o in index["operations"]} != set(operations):
        problems.append("Operation index differs from OpenAPI")
    tasks = {t["id"]: t for t in pack["tasks"]}
    if len(tasks) != len(pack["tasks"]):
        problems.append("Duplicate task id")
    visiting, done, order = set(), set(), []

    def visit(tid):
        if tid in visiting:
            problems.append(f"Dependency cycle: {tid}")
            return
        if tid in done:
            return
        if tid not in tasks:
            problems.append(f"Unknown dependency: {tid}")
            return
        visiting.add(tid)
        for dependency in tasks[tid]["dependsOn"]:
            visit(dependency)
        visiting.remove(tid)
        done.add(tid)
        order.append(tid)

    root = pack["workspaceRoot"].replace("\\", "/").rstrip("/") + "/"
    for tid, task in tasks.items():
        visit(tid)
        if task["status"] != "not_started":
            problems.append(f"Design pack must not claim implemented task: {tid}")
        if len(task.get("acceptance", [])) < 2:
            problems.append(f"Insufficient acceptance checks: {tid}")
        for check in task.get("acceptance", []):
            if not all(check.get(key) for key in ("given", "when", "then")):
                problems.append(f"Incomplete GWT: {tid}")
            if check.get("status") != "not_run":
                problems.append(f"Unexpected executed test claim: {tid}")
        for oid in task.get("operationIds", []):
            if oid not in operations:
                problems.append(f"Unknown operation: {tid}/{oid}")
        for file in task["allowedWritePaths"]:
            normalized = file.replace("\\", "/")
            if not normalized.startswith(root) or ".." in normalized.split("/"):
                problems.append(f"Write scope outside workspace: {tid}/{file}")
            if normalized.endswith("/AGENTS.md") or "/438C-2021全套模板/" in normalized:
                problems.append(f"Protected original in task write scope: {tid}/{file}")
    if not fixtures.get("synthetic") or fixtures.get("forProductionSeed"):
        problems.append("Fixture safety labels missing")
    for case in fixtures["scenarioCases"]:
        if case["taskId"] not in tasks:
            problems.append(f"Unknown scenario task: {case['id']}")
        for oid in case.get("operations", []):
            if oid not in operations:
                problems.append(f"Unknown scenario operation: {case['id']}/{oid}")
    if not block.get("exampleOnly") or block.get("productionUsable"):
        problems.append("Block example must not claim production readiness")
    schema_checked = 0
    case_checked = 0
    structural_only = "--structural-only" in sys.argv
    try:
        if structural_only:
            raise RuntimeError("STRUCTURAL_ONLY")
        from jsonschema import Draft202012Validator, FormatChecker
        for name, schema in schemas.items():
            try:
                Draft202012Validator.check_schema(schema)
                schema_checked += 1
            except Exception as exc:
                problems.append(f"Invalid JSON Schema {name}: {exc}")
        for case in fixtures["schemaCases"]:
            name = case["schema"]
            if name not in schemas:
                problems.append(f"Missing schema: {case['id']}/{name}")
                continue
            local = {"$ref": f"#/components/schemas/{name}", "components": api["components"]}
            errors = list(Draft202012Validator(local, format_checker=FormatChecker()).iter_errors(case["value"]))
            actual = not errors
            if actual != case["expectedValid"]:
                detail = "; ".join(e.message[:220] for e in errors[:2])
                problems.append(f"Fixture result mismatch {case['id']}: actual={actual}; {detail}")
            case_checked += 1
    except RuntimeError as exc:
        if str(exc) != "STRUCTURAL_ONLY":
            raise
    except ImportError as exc:
        problems.append(f"JSON Schema dependency unavailable: {exc}; schema cases NOT checked. No automatic install is performed.")
    report = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "Offline structural/reference/task checks only" if structural_only else "Offline structural/reference/JSON-Schema/example checks only",
        "schemaExamplesRequested": not structural_only,
        "schemaExamplesNotRunReason": "Explicit --structural-only; JSON Schema examples are NOT validated" if structural_only else None,
        "fullOpenApiMetaSchemaValidated": False,
        "runtimeApiTested": False,
        "frontendScreenshotsTested": False,
        "realModelTested": False,
        "pathCount": len(api["paths"]),
        "operationCount": len(operations),
        "schemaCount": len(schemas),
        "schemasChecked": schema_checked,
        "schemaExamplesChecked": case_checked,
        "businessScenarioSpecifications": len(fixtures["scenarioCases"]),
        "taskCount": len(tasks),
        "taskOrder": order,
        "issues": problems,
        "passed": not problems,
    }
    (BASE / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

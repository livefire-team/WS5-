import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from livefire.agents import strict_schema
from livefire.contracts import (AutonomyPolicy, ChangeReport, PlannerOutput, RecoveryOption, RemediationBrief,
                                engineer_may_write)

FIX = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def fixture(name, drill="drill-t1"):
    return json.loads((FIX / name).read_text().replace("drill-fixture", drill))


def test_fixtures_are_valid_contracts():
    PlannerOutput.model_validate(fixture("planner-options.json"))
    PlannerOutput.model_validate(fixture("planner-brief.json"))
    ChangeReport.model_validate(fixture("change-report.json"))


def test_planner_output_fills_exactly_its_stage():
    data = fixture("planner-options.json")
    data["remediation_brief"] = fixture("planner-brief.json")["remediation_brief"]
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(data)
    data = fixture("planner-options.json")
    data["stage"] = "brief"
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(data)


def test_option_id_must_match_its_action():
    with pytest.raises(ValidationError):
        RecoveryOption(id="narrow", label="x", action="grant_contributor_subscription", risk="x",
                       evidence=[{"source": "identity", "ref": "x", "note": "x"}])


def test_engineer_paths_are_bounded():
    assert engineer_may_write("src/order-service/tests/test_secret_recovery.py")
    for path in ("journeys/test_order_journey.py", ".github/workflows/order-service.yml", "src/order-service/app/main.py",
                 "src/order-service/tests/conftest.py", "infra/main.bicep", "src/drill-runner/livefire/policy.py"):
        assert not engineer_may_write(path), path
    brief = fixture("planner-brief.json")["remediation_brief"]
    brief["allowed_repository_paths"].append("journeys/test_order_journey.py")
    with pytest.raises(ValidationError):
        RemediationBrief.model_validate(brief)


def test_change_report_belongs_to_its_drill_and_attempt():
    data = fixture("change-report.json")
    data["branch"] = "candidate/drill-other-1"
    with pytest.raises(ValidationError):
        ChangeReport.model_validate(data)
    data = fixture("change-report.json")
    data["changed_paths"] = ["journeys/test_order_journey.py"]
    with pytest.raises(ValidationError):
        ChangeReport.model_validate(data)


def test_broad_action_can_never_be_preauthorized():
    with pytest.raises(ValidationError):
        AutonomyPolicy(preauthorized_actions=["grant_contributor_subscription"], expires_at="2026-11-18T23:00:00Z")


@pytest.mark.parametrize("model", [PlannerOutput, ChangeReport])
def test_agent_schemas_are_strict(model):
    schema = strict_schema(model)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and "default" not in text

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)


def test_published_schemas_match_the_code():
    from livefire.schemas import export
    for name, schema in export().items():
        assert json.loads((SCHEMAS / name).read_text()) == schema, f"run scripts/export_schemas.py: {name} is stale"

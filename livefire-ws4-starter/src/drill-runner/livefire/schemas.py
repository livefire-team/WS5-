"""JSON schemas for contracts/schemas: written by scripts/export_schemas.py, checked by the tests."""
from .agents import strict_schema
from .contracts import (ApprovalDecision, ApprovalRequest, AutonomyPolicy, ChangeReport, EvidencePackage, Event,
                         OpsResult, PlannerOutput, RefereeResult)


def export() -> dict[str, dict]:
    out = {f"{m.__name__}.schema.json": m.model_json_schema()
           for m in (Event, RefereeResult, OpsResult, ApprovalRequest, ApprovalDecision, AutonomyPolicy,
                     PlannerOutput, ChangeReport, EvidencePackage)}
    out["PlannerOutput.strict.json"] = strict_schema(PlannerOutput)
    out["ChangeReport.strict.json"] = strict_schema(ChangeReport)
    return out

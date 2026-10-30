"""Calls the two Foundry prompt agents and turns their output into validated contract objects."""
import asyncio
import copy
import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from .contracts import ChangeReport, PlannerOutput
from .ports import DependencyError, Usage

# Keywords the model-facing schema keeps. Everything else (lengths, patterns, defaults) is still enforced,
# by Pydantic, after the response arrives. Keeping the schema minimal makes it valid in strict mode everywhere.
_KEEP = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "const", "description"}


def strict_schema(model: type[BaseModel]) -> dict:
    """A strict-mode JSON schema for structured outputs: no $ref, every property required, no extras."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        out = {k: walk(v) for k, v in node.items() if k in _KEEP}
        if "properties" in node:
            out["properties"] = {name: walk(prop) for name, prop in node["properties"].items()}
            out["required"] = list(node["properties"])
            out["additionalProperties"] = False
        if "enum" in node and "type" not in out:
            out["type"] = "string"
        if "const" in node:
            out = {"type": "string", "enum": [node["const"]]}
        return out

    return walk(raw)


@dataclass
class AgentRef:
    name: str
    version: str

    @classmethod
    def parse(cls, value: str) -> "AgentRef":
        name, _, version = value.partition(":")
        if not name or not version:
            raise ValueError("agent references are name:version, pinned")
        return cls(name, version)


class FoundryAgents:
    """Responses API calls to pinned agent versions. The agents' tools and instructions live in Foundry;
    nothing here can add a tool or change an instruction at run time."""

    def __init__(self, project_endpoint: str, planner: str, engineer: str, credential=None):
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        self.project = AIProjectClient(endpoint=project_endpoint, credential=credential or DefaultAzureCredential())
        self.openai = self.project.get_openai_client()
        self.refs = {"planner": AgentRef.parse(planner), "engineer": AgentRef.parse(engineer)}

    def instruction_hashes(self) -> dict[str, str]:
        """sha256 of the instructions each pinned version actually runs with, for the evidence package."""
        import hashlib
        hashes = {}
        for role, ref in self.refs.items():
            version = self.project.agents.get_version(agent_name=ref.name, agent_version=ref.version)
            hashes[role] = hashlib.sha256((version.definition.instructions or "").encode()).hexdigest()
        return hashes

    def _respond(self, role: str, text: str, previous: str | None = None):
        ref = self.refs[role]
        kwargs = {"previous_response_id": previous} if previous else {}
        return self.openai.responses.create(
            input=text,
            extra_body={"agent_reference": {"name": ref.name, "version": ref.version, "type": "agent_reference"}},
            **kwargs,
        )

    async def _run(self, role: str, task: dict, model: type[BaseModel]):
        usage = Usage(agent=role)
        text, previous = json.dumps(task), None
        for attempt in range(2):                          # one repair turn if the output fails validation
            try:
                response = await asyncio.to_thread(self._respond, role, text, previous)
            except Exception as exc:                      # network, auth, 5xx, tool-call timeout
                raise DependencyError(f"Foundry call for the {role} failed: {exc}") from exc
            usage.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
            usage.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
            usage.tool_calls += sum(1 for item in response.output if getattr(item, "type", "") == "mcp_call")
            usage.response_id = response.id
            try:
                return model.model_validate_json(response.output_text), usage
            except ValidationError as exc:
                previous = response.id
                text = ("Your last output failed validation. Return the corrected JSON object only. "
                        f"Errors: {json.dumps(exc.errors(include_url=False, include_input=False))[:2000]}")
        raise DependencyError(f"the {role} returned an invalid contract twice")

    async def planner(self, task: dict) -> tuple[PlannerOutput, Usage]:
        return await self._run("planner", task, PlannerOutput)

    async def engineer(self, task: dict) -> tuple[ChangeReport, Usage]:
        return await self._run("engineer", task, ChangeReport)

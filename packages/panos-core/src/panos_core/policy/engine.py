"""Pure-function policy evaluator (ADR-005).

``PolicyEngine.evaluate`` maps an action request to ALLOW / REQUIRE_APPROVAL / DENY:

* unknown action class → DENY (default deny);
* P4 → DENY; P3 → REQUIRE_APPROVAL; P2 → REQUIRE_APPROVAL (ask by default);
* P0/P1 → ALLOW inside the authorized scope, REQUIRE_APPROVAL outside it.

Project overrides may raise any level. Lowering is honoured only for rules marked
``loosenable`` and only from P2 to P1; P3 and P4 can never be lowered by configuration.
"""

from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from panos_core.domain.enums import PermissionLevel, PolicyDecision


def parse_level(value: str | int | PermissionLevel) -> PermissionLevel:
    if isinstance(value, PermissionLevel):
        return value
    if isinstance(value, int):
        return PermissionLevel(value)
    text = value.strip().upper()
    if len(text) == 2 and text[0] == "P" and text[1].isdigit():
        return PermissionLevel(int(text[1]))
    raise ValueError(f"invalid permission level: {value!r}")


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
    level: PermissionLevel
    description: str = ""
    loosenable: bool = False


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    rules: dict[str, PolicyRule]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Policy":
        rules: dict[str, PolicyRule] = {}
        for raw in data.get("rules", []):
            rule = PolicyRule(
                action=raw["action"],
                level=parse_level(raw["level"]),
                description=raw.get("description", ""),
                loosenable=bool(raw.get("loosenable", False)),
            )
            if rule.action in rules:
                raise ValueError(f"duplicate policy rule for {rule.action!r}")
            if rule.loosenable and rule.level != PermissionLevel.P2_CONTROLLED:
                raise ValueError(f"only P2 rules may be loosenable: {rule.action!r}")
            rules[rule.action] = rule
        return cls(version=int(data["version"]), rules=rules)

    def level_of(self, action: str) -> PermissionLevel | None:
        rule = self.rules.get(action)
        return rule.level if rule else None


@cache
def load_default_policy() -> Policy:
    text = (
        resources.files("panos_core.policy")
        .joinpath("defaults/policy.yaml")
        .read_text(encoding="utf-8")
    )
    return Policy.from_mapping(yaml.safe_load(text))


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    actor: str
    in_scope: bool = True
    project_id: str | None = None
    overrides: Mapping[str, PermissionLevel] = Field(default_factory=dict)


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    decision: PolicyDecision
    level: PermissionLevel
    reasons: tuple[str, ...]
    rule: str | None
    policy_version: int


class PolicyEngine:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or load_default_policy()

    def evaluate(self, request: PolicyRequest) -> PolicyResult:
        rule = self.policy.rules.get(request.action)
        if rule is None:
            return self._result(
                request,
                PolicyDecision.DENY,
                PermissionLevel.P4_FORBIDDEN,
                [f"unknown action class {request.action!r} (default deny)"],
                rule=None,
            )

        reasons = [f"rule {rule.action}: {rule.level.name}"]
        level = self._apply_override(rule, request.overrides.get(request.action), reasons)

        if level == PermissionLevel.P4_FORBIDDEN:
            decision = PolicyDecision.DENY
            reasons.append("forbidden action")
        elif level >= PermissionLevel.P2_CONTROLLED:
            decision = PolicyDecision.REQUIRE_APPROVAL
            reasons.append("explicit approval required")
        elif not request.in_scope:
            decision = PolicyDecision.REQUIRE_APPROVAL
            reasons.append("target outside the authorized scope")
        else:
            decision = PolicyDecision.ALLOW
        return self._result(request, decision, level, reasons, rule=rule.action)

    @staticmethod
    def _apply_override(
        rule: PolicyRule, override: PermissionLevel | None, reasons: list[str]
    ) -> PermissionLevel:
        if override is None or override == rule.level:
            return rule.level
        if override > rule.level:
            reasons.append(f"project override tightened to {override.name}")
            return override
        if (
            rule.loosenable
            and rule.level == PermissionLevel.P2_CONTROLLED
            and override == PermissionLevel.P1_SAFE_WRITE
        ):
            reasons.append("project override loosened P2 to P1 (user-granted)")
            return override
        reasons.append(f"project override to {override.name} ignored: rule cannot be loosened")
        return rule.level

    def _result(
        self,
        request: PolicyRequest,
        decision: PolicyDecision,
        level: PermissionLevel,
        reasons: list[str],
        *,
        rule: str | None,
    ) -> PolicyResult:
        return PolicyResult(
            action=request.action,
            decision=decision,
            level=level,
            reasons=tuple(reasons),
            rule=rule,
            policy_version=self.policy.version,
        )

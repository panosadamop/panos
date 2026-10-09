import pytest
from hypothesis import given
from hypothesis import strategies as st

from panos_core.domain import PermissionLevel, PolicyDecision
from panos_core.policy import Policy, PolicyEngine, PolicyRequest, load_default_policy, parse_level

P = PermissionLevel
ENGINE = PolicyEngine()
POLICY = load_default_policy()


def decide(action: str, **kw: object) -> PolicyDecision:
    return ENGINE.evaluate(PolicyRequest(action=action, actor="agent", **kw)).decision  # type: ignore[arg-type]


# PERMISSIONS.md "Initial policy" table, row by row.
@pytest.mark.req("NFR-SEC-001", "FR-011")
@pytest.mark.parametrize(
    ("action", "level"),
    [
        ("fs.read", P.P0_READ),
        ("fs.search", P.P0_READ),
        ("app.launch", P.P1_SAFE_WRITE),
        ("fs.write", P.P1_SAFE_WRITE),
        ("build.test", P.P1_SAFE_WRITE),
        ("pkg.install", P.P2_CONTROLLED),
        ("db.dev.schema", P.P2_CONTROLLED),
        ("fs.delete", P.P2_CONTROLLED),
        ("fs.delete.unrecoverable", P.P3_APPROVAL_REQUIRED),
        ("git.commit", P.P3_APPROVAL_REQUIRED),
        ("git.mutate.shared", P.P3_APPROVAL_REQUIRED),
        ("docker.exec", P.P3_APPROVAL_REQUIRED),
        ("k8s.mutate", P.P3_APPROVAL_REQUIRED),
        ("pm.write", P.P3_APPROVAL_REQUIRED),
        ("calendar.write", P.P3_APPROVAL_REQUIRED),
        ("deploy.production", P.P3_APPROVAL_REQUIRED),
        ("infra.mutate", P.P3_APPROVAL_REQUIRED),
        ("email.send", P.P4_FORBIDDEN),
        ("browser.submit", P.P4_FORBIDDEN),
        ("browser.upload", P.P4_FORBIDDEN),
    ],
)
def test_default_policy_matches_permissions_table(action: str, level: PermissionLevel) -> None:
    assert POLICY.level_of(action) == level


@pytest.mark.req("BR-017", "BR-018", "AC-US040-02", "AC-US041-02")
@pytest.mark.parametrize(
    "action",
    ["git.commit", "git.mutate.shared", "docker.exec", "k8s.mutate", "pkg.install", "fs.delete"],
)
def test_p2_and_p3_require_approval(action: str) -> None:
    assert decide(action) == PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.req("BR-019", "BR-022", "AC-US043-03")
@pytest.mark.parametrize(
    "action", ["email.send", "browser.submit", "browser.upload", "fs.write.git_internal"]
)
def test_p4_is_denied(action: str) -> None:
    assert decide(action) == PolicyDecision.DENY


@pytest.mark.req("NFR-SEC-001")
def test_unknown_action_is_denied_by_default() -> None:
    result = ENGINE.evaluate(PolicyRequest(action="totally.new", actor="agent"))
    assert result.decision == PolicyDecision.DENY
    assert result.rule is None
    assert "default deny" in result.reasons[0]


def test_p0_p1_allowed_in_scope_but_need_approval_out_of_scope() -> None:
    assert decide("fs.read") == PolicyDecision.ALLOW
    assert decide("fs.write") == PolicyDecision.ALLOW
    assert decide("fs.write", in_scope=False) == PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.req("NFR-SEC-001")
def test_overrides_can_tighten_anything() -> None:
    assert decide("fs.read", overrides={"fs.read": P.P3_APPROVAL_REQUIRED}) == (
        PolicyDecision.REQUIRE_APPROVAL
    )
    assert decide("build.test", overrides={"build.test": P.P4_FORBIDDEN}) == PolicyDecision.DENY


def test_loosenable_p2_can_become_p1() -> None:
    assert decide("fs.delete", overrides={"fs.delete": P.P1_SAFE_WRITE}) == PolicyDecision.ALLOW


@pytest.mark.req("NFR-SEC-001", "AC-US041-03")
@pytest.mark.parametrize(
    ("action", "override"),
    [
        ("git.commit", P.P0_READ),
        ("docker.exec", P.P1_SAFE_WRITE),
        ("email.send", P.P0_READ),
        ("browser.upload", P.P3_APPROVAL_REQUIRED),
        ("pkg.install", P.P1_SAFE_WRITE),  # P2 but not marked loosenable
        ("fs.delete", P.P0_READ),
    ],  # loosenable only to P1
)
def test_overrides_cannot_loosen_protected_rules(action: str, override: PermissionLevel) -> None:
    result = ENGINE.evaluate(PolicyRequest(action=action, actor="a", overrides={action: override}))
    assert result.level == POLICY.level_of(action)
    assert any("ignored" in r for r in result.reasons)


@given(st.sampled_from(sorted(POLICY.rules)), st.sampled_from(list(P)), st.booleans())
def test_property_override_never_lowers_p3_or_p4(action: str, override: P, in_scope: bool) -> None:
    base = POLICY.level_of(action)
    assert base is not None
    result = ENGINE.evaluate(
        PolicyRequest(action=action, actor="a", in_scope=in_scope, overrides={action: override})
    )
    if base >= P.P3_APPROVAL_REQUIRED:
        assert result.decision != PolicyDecision.ALLOW
    if base == P.P4_FORBIDDEN:
        assert result.decision == PolicyDecision.DENY
    assert result.level >= min(base, P.P1_SAFE_WRITE)


@given(st.sampled_from(sorted(POLICY.rules)), st.booleans())
def test_property_decision_matches_level(action: str, in_scope: bool) -> None:
    result = ENGINE.evaluate(PolicyRequest(action=action, actor="a", in_scope=in_scope))
    if result.level == P.P4_FORBIDDEN:
        assert result.decision == PolicyDecision.DENY
    elif result.level >= P.P2_CONTROLLED or not in_scope:
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    else:
        assert result.decision == PolicyDecision.ALLOW
    assert result.policy_version == POLICY.version


@pytest.mark.parametrize(
    ("raw", "level"),
    [
        ("P0", P.P0_READ),
        ("p3", P.P3_APPROVAL_REQUIRED),
        (4, P.P4_FORBIDDEN),
        (P.P2_CONTROLLED, P.P2_CONTROLLED),
    ],
)
def test_parse_level(raw: str | int | P, level: P) -> None:
    assert parse_level(raw) == level


def test_parse_level_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="invalid permission level"):
        parse_level("high")


def test_policy_validation_rejects_duplicates_and_bad_loosenable() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Policy.from_mapping(
            {
                "version": 1,
                "rules": [{"action": "a.b", "level": "P0"}, {"action": "a.b", "level": "P1"}],
            }
        )
    with pytest.raises(ValueError, match="loosenable"):
        Policy.from_mapping(
            {"version": 1, "rules": [{"action": "a.b", "level": "P3", "loosenable": True}]}
        )

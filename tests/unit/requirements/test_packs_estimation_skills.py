"""Stack packs, scaffolding, estimation and skills (M6-06/07/08/09)."""

from pathlib import Path

import pytest
import yaml

from panos_core.config.settings import EstimationSettings
from panos_core.domain.requirements import Requirement
from panos_core.domain.requirements import RequirementStatus as S
from panos_core.domain.requirements import RequirementType as T
from panos_core.requirements.estimation import estimate
from panos_core.requirements.packs import (
    addons,
    detect_stack,
    package_name,
    packs,
    render_files,
    scaffold,
)
from panos_core.requirements.skills import build_skills, render_skill_file

PID = "prj_" + "1" * 32

# Structure-level expectations per pack (M6-07): manifest, entry point, a test.
EXPECTED = {
    "python-fastapi": {"pyproject.toml", "src/order_portal/main.py", "tests/test_health.py"},
    "python-django": {"manage.py", "order_portal/settings.py", "requirements.txt"},
    "java-spring-boot": {
        "pom.xml",
        "src/main/java/com/example/order_portal/Application.java",
        "src/test/java/com/example/order_portal/ApplicationTests.java",
    },
    "java-vaadin": {"pom.xml", "src/main/java/com/example/order_portal/views/MainView.java"},
    "react-typescript": {"package.json", "src/App.tsx", "src/App.test.tsx", "index.html"},
    "node-nestjs": {"package.json", "src/main.ts", "test/health.controller.spec.ts"},
    "php-laravel": {"composer.json", "routes/web.php", "tests/Feature/HealthTest.php"},
}


def req(
    key: str,
    type_: T,
    title: str,
    *,
    status: S = S.APPROVED,
    version: int = 1,
    parent: str | None = None,
    **attrs: object,
) -> Requirement:
    return Requirement(
        project_id=PID,
        key=key,
        type=type_,
        title=title,
        status=status,
        version=version,
        parent_key=parent,
        attributes=dict(attrs),
    )


@pytest.mark.req("BR-008")
def test_every_stack_family_has_a_pack_and_addons_exist() -> None:
    assert set(packs()) == set(EXPECTED)
    assert set(addons()) == {"postgresql", "mongodb", "docker", "kubernetes"}
    for pack in packs().values():
        assert pack.commands.get("test"), pack.id
        assert pack.conventions and pack.preferred_ide.startswith(("jetbrains.", "vscode"))


@pytest.mark.req("US-021", "BR-008")
@pytest.mark.parametrize(
    ("brief", "pack", "extra"),
    [
        ("A FastAPI service with PostgreSQL in Docker", "python-fastapi", {"postgresql", "docker"}),
        ("Django admin for the warehouse", "python-django", set()),
        ("Spring Boot + Vaadin back office", "java-vaadin", set()),
        ("A Spring Boot REST API on Kubernetes", "java-spring-boot", {"kubernetes"}),
        (
            "React/TypeScript front end and a NestJS backend with MongoDB",
            "node-nestjs",
            {"mongodb"},
        ),
        ("Laravel shop", "php-laravel", set()),
        ("A React dashboard", "react-typescript", set()),
        ("A small tool for our team", None, set()),
        ("Our springboard project (not a stack)", None, set()),
    ],
)
def test_explicit_stack_detection(brief: str, pack: str | None, extra: set[str]) -> None:
    found, found_addons = detect_stack(brief)
    assert found == pack and set(found_addons) == extra


@pytest.mark.req("FR-007")
@pytest.mark.parametrize("pack_id", sorted(EXPECTED))
def test_each_pack_scaffolds_its_structure_offline(pack_id: str, tmp_path: Path) -> None:
    result = scaffold(tmp_path, pack_id, ["docker"], name="Order Portal", description="demo")
    created = set(result.created)
    assert EXPECTED[pack_id] <= created, created
    assert "Dockerfile" in created and ".gitignore" in created
    for rel in created:
        assert "{{" not in (tmp_path / rel).read_text(encoding="utf-8"), rel  # fully rendered
    if pack_id == "php-laravel":
        import json

        json.loads((tmp_path / "composer.json").read_text(encoding="utf-8"))
    again = scaffold(tmp_path, pack_id, [], name="Order Portal", description="demo")
    assert again.created == [] and set(again.skipped) >= EXPECTED[pack_id]


def test_rendering_details() -> None:
    files = render_files(
        "python-fastapi", ["kubernetes"], name="Order Portal!", description="Orders"
    )
    assert 'name = "order-portal"' in files["pyproject.toml"]
    yaml.safe_load(files["deploy/k8s/deployment.yaml"])
    assert package_name("2nd Shop") == "app_2nd_shop" and package_name("---") == "app"
    with pytest.raises(KeyError):
        render_files("cobol", [], name="x", description="x")


@pytest.mark.req("BR-014")
def test_estimation_defaults_rates_priorities_and_confidence() -> None:
    cfg = EstimationSettings(velocity_points=8)
    items = [
        req("US-001", T.US, "pay", points=5, priority="must"),
        req("US-002", T.US, "list", points=3, priority="could"),
        req("US-003", T.US, "unestimated"),
        req("US-004", T.US, "later", points=2, priority="wont"),
    ]
    est = estimate(items, cfg)
    assert [s.points for s in est.stories] == [5, 3, 3, 2]
    assert est.total_points == 13 and est.total_hours == 78.0
    assert [s.stories for s in est.sprints] == [["US-001", "US-003"], ["US-002"]]  # 5+3 fits 8
    assert est.confidence == "medium" and est.confidence_score == 0.75
    assert any("US-003" in a for a in est.assumptions)
    assert estimate(items, cfg, open_questions=6).confidence == "low"
    empty = estimate([], cfg)
    assert empty.total_points == 0 and empty.sprints == [] and empty.confidence == "low"


@pytest.mark.req("BR-013", "FR-030", "AC-US033-01", "AC-US033-02")
def test_skills_use_only_approved_facts_and_hash_changes_with_them() -> None:
    items = [
        req(
            "ADR-001",
            T.ADR,
            "Stack: Python / FastAPI",
            pack="python-fastapi",
            addons=["postgresql"],
        ),
        req("NFR-001", T.NFR, "Fast checkout", category="PERF"),
        req("NFR-002", T.NFR, "Draft secret target", status=S.DRAFT),
        req("FR-001", T.FR, "Checkout"),
        req("FR-002", T.FR, "Stale feature"),
        req("RULE-001", T.RULE, "One item minimum"),
    ]
    items[4] = items[4].model_copy(update={"stale": True})
    drafts = {d.name: d for d in build_skills("Web Shop", items)}
    assert set(drafts) == {"web-shop-architecture", "web-shop-standards", "web-shop-domain"}
    arch = drafts["web-shop-architecture"]
    assert arch.source_keys == ["ADR-001", "NFR-001"] and "NFR-002" not in arch.body
    domain = drafts["web-shop-domain"]
    assert domain.source_keys == ["FR-001"] and "Stale feature" not in domain.body
    std = drafts["web-shop-standards"]
    assert "uv run pytest" in std.body and "RULE-001" in std.body and "PostgreSQL" not in std.name
    text = render_skill_file(arch, 3)
    assert text.startswith("---\nname: web-shop-architecture\n") and "version: 3" in text

    changed = [
        *items[:1],
        items[1].model_copy(update={"version": 2, "title": "Faster"}),
        *items[2:],
    ]
    after = {d.name: d for d in build_skills("Web Shop", changed)}
    assert after["web-shop-architecture"].facts_hash != arch.facts_hash
    assert after["web-shop-domain"].facts_hash == domain.facts_hash

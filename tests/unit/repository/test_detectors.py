"""Stack and command detection against fixture repositories (AC-US012-01, BR-008)."""

from pathlib import Path

import pytest

from panos_core.repository.detectors import Detection, detect
from panos_core.repository.walk import iter_project_files
from tests.repos import FIXTURES

pytestmark = pytest.mark.req("FR-005", "AC-US012-01", "BR-008")


def run(name: str) -> Detection:
    root = FIXTURES / name
    return detect(root, list(iter_project_files(root)))


def test_python_fastapi_with_uv() -> None:
    det = run("python_fastapi")
    stack = det.as_stack()
    assert stack["primary_language"] == "Python"
    assert {"FastAPI", "SQLAlchemy", "pytest"} <= set(stack["frameworks"])
    assert stack["package_managers"] == ["uv"]
    assert "PostgreSQL" in stack["databases"]
    assert det.commands["test"] == [["uv", "run", "pytest"]]
    assert det.commands["lint"] == [["uv", "run", "ruff", "check", "."]]
    assert det.commands["typecheck"] == [["uv", "run", "mypy"]]


def test_django_with_pip() -> None:
    det = run("django_pip")
    assert "Django" in det.frameworks
    assert det.package_managers == {"pip"}
    assert det.commands["test"] == [["python", "manage.py", "test"]]
    assert "PostgreSQL" in det.databases


def test_spring_boot_vaadin_maven_wrapper() -> None:
    det = run("spring_maven")
    assert {"Spring Boot", "Vaadin", "JUnit"} <= det.frameworks
    assert det.build_systems == {"maven"}
    assert det.commands["test"] == [["./mvnw", "test"]]
    assert det.commands["build"] == [["./mvnw", "package"]]
    assert "PostgreSQL" in det.databases
    assert det.languages["Java"] == 2


def test_gradle_kotlin_multi_project() -> None:
    det = run("gradle_kotlin")
    assert {"Spring Boot", "Kotlin"} <= det.frameworks
    assert det.commands["test"] == [["./gradlew", "test"]]
    assert "gradle multi-project" in det.workspaces


def test_react_vite_with_pnpm_scripts() -> None:
    det = run("react_vite_pnpm")
    assert {"React", "Vite", "TypeScript", "Vitest", "Tailwind CSS"} <= det.frameworks
    assert det.package_managers == {"pnpm"}
    assert det.commands["test"] == [["pnpm", "test"]]
    assert det.commands["lint"] == [["pnpm", "run", "lint"]]
    assert det.commands["typecheck"] == [["pnpm", "run", "typecheck"]]
    assert det.commands["build"] == [["pnpm", "run", "build"]]
    assert "dev" not in str(det.commands)  # dev servers are not allowlisted commands


def test_nestjs_with_npm_and_mongo() -> None:
    det = run("nestjs_npm")
    assert "NestJS" in det.frameworks
    assert det.package_managers == {"npm"}
    assert det.commands["test"] == [["npm", "test"]]
    assert "MongoDB" in det.databases


def test_laravel() -> None:
    det = run("laravel")
    assert "Laravel" in det.frameworks
    assert det.commands["test"] == [["php", "artisan", "test"]]
    assert det.commands["format"] == [["vendor/bin/pint"]]
    assert det.languages["PHP"] == 1


def test_infrastructure_only_repo() -> None:
    det = run("infra_only")
    assert {"Docker", "Docker Compose", "Kubernetes", "Terraform", "Azure"} <= det.infrastructure
    assert {"PostgreSQL", "Redis", "MongoDB"} <= det.databases
    assert "GitHub Actions" in det.ci


def test_go_rust_dotnet() -> None:
    det = run("go_rust_dotnet")
    assert {"go", "cargo", "dotnet"} <= det.build_systems
    assert ["go", "test", "./..."] in det.commands["test"]
    assert ["cargo", "test"] in det.commands["test"]
    assert ["dotnet", "test"] in det.commands["test"]


def test_unknown_stack_has_no_manifests_or_commands() -> None:
    det = run("unknown_stack")
    assert det.manifests == []
    assert det.commands == {}


def test_detected_commands_are_classified_as_safe_once_allowlisted() -> None:
    """Discovered commands become build.test via the project allowlist (ADR-007)."""
    from panos_core.shell.classifier import classify_argv
    from panos_core.tools.builtin.shell import most_restrictive

    det = run("python_fastapi")
    allowed = tuple(tuple(c) for cmds in det.commands.values() for c in cmds)
    for argv in allowed:
        assert most_restrictive(classify_argv(list(argv), allowed=allowed)).action == "build.test"


def test_malicious_xml_entities_are_not_expanded(tmp_path: Path) -> None:
    """pom.xml is scanned with regexes, never parsed as XML (no entity expansion)."""
    (tmp_path / "pom.xml").write_text(
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 '
        '"&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><project>&lol2;</project>',
        encoding="utf-8",
    )
    det = detect(tmp_path, list(iter_project_files(tmp_path)))
    assert det.build_systems == {"maven"}


def test_fixture_and_sample_content_does_not_shape_the_stack(tmp_path: Path) -> None:
    """Sample infrastructure under tests/fixtures describes other systems, not this one."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    sample = tmp_path / "tests" / "fixtures" / "demo"
    (sample / "k8s").mkdir(parents=True)
    (sample / "main.tf").write_text('provider "aws" {}\n', encoding="utf-8")
    (sample / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (sample / "Dockerfile").write_text("FROM x\n", encoding="utf-8")
    (sample / "k8s" / "d.yaml").write_text("apiVersion: v1\nkind: Pod\n", encoding="utf-8")
    (sample / "package.json").write_text("{}", encoding="utf-8")
    det = detect(tmp_path, list(iter_project_files(tmp_path)))
    assert det.infrastructure == set()
    assert det.nested_manifests == []

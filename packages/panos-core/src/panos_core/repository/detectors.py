"""Stack and command detection from repository manifests (FR-005, BR-008, AC-US012-01).

Detectors only *read* files. Nothing in the repository is executed during analysis.
XML manifests (``pom.xml``) are inspected with regexes rather than an XML parser, so
hostile entity definitions cannot be expanded.
"""

import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from panos_core.repository.walk import WalkedFile

LANGUAGE_BY_EXT: Final = {
    ".py": "Python",
    ".pyi": "Python",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".php": "PHP",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".fs": "F#",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".scala": "Scala",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cc": "C++",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".tf": "HCL",
    ".dart": "Dart",
    ".groovy": "Groovy",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
}
COMMAND_KINDS: Final = ("test", "lint", "format", "typecheck", "build")
# Sample/fixture content describes *other* systems; it must not shape this project's stack.
FIXTURE_DIRS: Final = frozenset(
    {
        "fixtures",
        "__fixtures__",
        "testdata",
        "test-data",
        "test_data",
        "__mocks__",
        "samples",
        "examples",
    }
)


def is_fixture_path(rel: str) -> bool:
    return any(part.lower() in FIXTURE_DIRS for part in rel.split("/")[:-1])


@dataclass
class Detection:
    languages: Counter[str] = field(default_factory=Counter)
    frameworks: set[str] = field(default_factory=set)
    package_managers: set[str] = field(default_factory=set)
    build_systems: set[str] = field(default_factory=set)
    databases: set[str] = field(default_factory=set)
    infrastructure: set[str] = field(default_factory=set)
    ci: set[str] = field(default_factory=set)
    manifests: list[str] = field(default_factory=list)
    nested_manifests: list[str] = field(default_factory=list)
    workspaces: set[str] = field(default_factory=set)  # root-declared multi-package
    commands: dict[str, list[list[str]]] = field(default_factory=dict)

    def add_command(self, kind: str, argv: list[str]) -> None:
        bucket = self.commands.setdefault(kind, [])
        if argv not in bucket:
            bucket.append(argv)

    def as_stack(self) -> dict[str, Any]:
        total = sum(self.languages.values()) or 1
        return {
            "languages": [
                {"name": n, "files": c, "share": round(c / total, 3)}
                for n, c in self.languages.most_common(10)
            ],
            "primary_language": self.languages.most_common(1)[0][0] if self.languages else None,
            "frameworks": sorted(self.frameworks),
            "package_managers": sorted(self.package_managers),
            "build_systems": sorted(self.build_systems),
            "databases": sorted(self.databases),
            "infrastructure": sorted(self.infrastructure),
            "ci": sorted(self.ci),
            "manifests": self.manifests,
            "nested_manifests": self.nested_manifests[:50],
            "workspaces": sorted(self.workspaces),
        }


def _read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(path))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(_read_text(path))
    except tomllib.TOMLDecodeError:
        return {}


_DB_HINTS: Final = {
    "postgres": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "pg": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mongo": "MongoDB",
    "mongoose": "MongoDB",
    "pymongo": "MongoDB",
    "motor": "MongoDB",
    "mysql": "MySQL",
    "mariadb": "MySQL",
    "redis": "Redis",
    "sqlite": "SQLite",
    "mssql": "SQL Server",
}


def _db_from_names(names: list[str], det: Detection) -> None:
    for name in names:
        low = name.lower()
        for hint, db in _DB_HINTS.items():
            if low == hint or low.startswith(hint) or f"/{hint}" in low or f"-{hint}" in low:
                det.databases.add(db)


# ===================================================================== Python
def _python_deps(root: Path) -> tuple[list[str], dict[str, Any]]:
    deps: list[str] = []
    pyproject = _read_toml(root / "pyproject.toml") if (root / "pyproject.toml").exists() else {}
    project = pyproject.get("project", {})
    deps += list(project.get("dependencies", []))
    for group in (project.get("optional-dependencies") or {}).values():
        deps += list(group)
    for group in (pyproject.get("dependency-groups") or {}).values():
        deps += [d for d in group if isinstance(d, str)]
    poetry = pyproject.get("tool", {}).get("poetry", {})
    deps += list((poetry.get("dependencies") or {}).keys())
    deps += list((poetry.get("dev-dependencies") or {}).keys())
    for req in sorted(root.glob("requirements*.txt")):
        deps += [
            ln.strip()
            for ln in _read_text(req).splitlines()
            if ln.strip() and not ln.startswith(("#", "-"))
        ]
    names = [re.split(r"[\s<>=!~\[;]", d, maxsplit=1)[0].lower() for d in deps if d]
    return names, pyproject


def detect_python(root: Path, det: Detection) -> None:
    markers = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"]
    present = [m for m in markers if (root / m).exists()]
    if not present and not list(root.glob("requirements*.txt")):
        return
    det.manifests += present
    names, pyproject = _python_deps(root)
    tool = pyproject.get("tool", {})
    if "workspace" in tool.get("uv", {}):
        det.workspaces.add("uv workspace")
    if (root / "uv.lock").exists():
        det.package_managers.add("uv")
        runner = ["uv", "run"]
    elif (root / "poetry.lock").exists() or "poetry" in tool:
        det.package_managers.add("poetry")
        runner = ["poetry", "run"]
    elif (root / "Pipfile").exists():
        det.package_managers.add("pipenv")
        runner = ["pipenv", "run"]
    else:
        det.package_managers.add("pip")
        runner = []
    det.build_systems.add("python")
    frameworks = {
        "fastapi": "FastAPI",
        "django": "Django",
        "flask": "Flask",
        "starlette": "Starlette",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "celery": "Celery",
        "streamlit": "Streamlit",
        "pytest": "pytest",
    }
    for dep, fw in frameworks.items():
        if dep in names:
            det.frameworks.add(fw)
    if (root / "manage.py").exists():
        det.frameworks.add("Django")
    _db_from_names(names, det)

    has_pytest = (
        "pytest" in names
        or "pytest" in tool
        or (root / "pytest.ini").exists()
        or (root / "conftest.py").exists()
        or (root / "tests" / "conftest.py").exists()
    )
    if has_pytest:
        det.add_command("test", [*runner, "pytest"] if runner else ["python", "-m", "pytest"])
    elif (root / "manage.py").exists():
        det.add_command("test", ["python", "manage.py", "test"])
    if "ruff" in names or "ruff" in tool or (root / "ruff.toml").exists():
        det.add_command("lint", [*runner, "ruff", "check", "."])
        det.add_command("format", [*runner, "ruff", "format", "."])
    elif "flake8" in names or (root / ".flake8").exists():
        det.add_command("lint", [*runner, "flake8"])
    if "black" in names or "black" in tool:
        det.add_command("format", [*runner, "black", "."])
    if "mypy" in names or "mypy" in tool or (root / "mypy.ini").exists():
        det.add_command("typecheck", [*runner, "mypy"])


# ===================================================================== Node
def detect_node(root: Path, det: Detection) -> None:
    pkg_path = root / "package.json"
    if not pkg_path.exists():
        return
    det.manifests.append("package.json")
    pkg = _read_json(pkg_path)
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    names = [str(n).lower() for n in deps]
    manager_field = str(pkg.get("packageManager", ""))
    if (root / "pnpm-lock.yaml").exists() or manager_field.startswith("pnpm"):
        pm = "pnpm"
    elif (root / "yarn.lock").exists() or manager_field.startswith("yarn"):
        pm = "yarn"
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        pm = "bun"
    else:
        pm = "npm"
    det.package_managers.add(pm)
    det.build_systems.add("node")
    if pkg.get("workspaces") or (root / "pnpm-workspace.yaml").exists():
        det.workspaces.add(f"{pm} workspaces")
    frameworks = {
        "react": "React",
        "next": "Next.js",
        "vue": "Vue",
        "@angular/core": "Angular",
        "@nestjs/core": "NestJS",
        "express": "Express",
        "vite": "Vite",
        "typescript": "TypeScript",
        "jest": "Jest",
        "vitest": "Vitest",
        "@playwright/test": "Playwright",
        "svelte": "Svelte",
        "electron": "Electron",
        "@tauri-apps/api": "Tauri",
        "tailwindcss": "Tailwind CSS",
    }
    for dep, fw in frameworks.items():
        if dep in names:
            det.frameworks.add(fw)
    _db_from_names(names, det)
    scripts = pkg.get("scripts") or {}
    if "test" in scripts:
        det.add_command("test", [pm, "test"])
    for script, kind in (
        ("lint", "lint"),
        ("format", "format"),
        ("typecheck", "typecheck"),
        ("type-check", "typecheck"),
        ("tsc", "typecheck"),
        ("build", "build"),
    ):
        if script in scripts:
            det.add_command(kind, [pm, "run", script])


# ===================================================================== JVM
def _wrapper(root: Path, name: str, fallback: str) -> list[str]:
    return (
        [f"./{name}"]
        if (root / name).exists()
        or (root / f"{name}.bat").exists()
        or (root / f"{name}.cmd").exists()
        else [fallback]
    )


def detect_jvm(root: Path, det: Detection) -> None:
    if (root / "pom.xml").exists():
        det.manifests.append("pom.xml")
        det.build_systems.add("maven")
        pom = _read_text(root / "pom.xml")
        mvn = _wrapper(root, "mvnw", "mvn")
        if "<modules>" in pom:
            det.workspaces.add("maven modules")
        det.add_command("test", [*mvn, "test"])
        det.add_command("build", [*mvn, "package"])
        _jvm_frameworks(pom, det)
    gradle_files = [
        f
        for f in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
        if (root / f).exists()
    ]
    if gradle_files:
        det.manifests += gradle_files
        det.build_systems.add("gradle")
        gradle = _wrapper(root, "gradlew", "gradle")
        det.add_command("test", [*gradle, "test"])
        det.add_command("build", [*gradle, "build"])
        text = "\n".join(_read_text(root / f) for f in gradle_files)
        if re.search(r"^\s*include[\s(]", text, re.M):
            det.workspaces.add("gradle multi-project")
        _jvm_frameworks(text, det)
        if "kotlin" in text.lower() or any(f.endswith(".kts") for f in gradle_files):
            det.frameworks.add("Kotlin")


def _jvm_frameworks(text: str, det: Detection) -> None:
    low = text.lower()
    if "spring-boot" in low or "org.springframework.boot" in low:
        det.frameworks.add("Spring Boot")
    if "vaadin" in low:
        det.frameworks.add("Vaadin")
    if "quarkus" in low:
        det.frameworks.add("Quarkus")
    if "junit" in low:
        det.frameworks.add("JUnit")
    _db_from_names(
        re.findall(r"[a-z0-9.\-]*(?:postgres|mongo|mysql|mariadb|redis)[a-z0-9.\-]*", low), det
    )


# ===================================================================== PHP / Go / Rust / .NET
def detect_php(root: Path, det: Detection) -> None:
    if not (root / "composer.json").exists():
        return
    det.manifests.append("composer.json")
    det.package_managers.add("composer")
    det.build_systems.add("php")
    composer = _read_json(root / "composer.json")
    names = [
        str(n).lower()
        for n in {**(composer.get("require") or {}), **(composer.get("require-dev") or {})}
    ]
    if "laravel/framework" in names or (root / "artisan").exists():
        det.frameworks.add("Laravel")
        det.add_command("test", ["php", "artisan", "test"])
    elif "phpunit/phpunit" in names or (root / "phpunit.xml").exists():
        det.add_command("test", ["vendor/bin/phpunit"])
    if any(n.startswith("symfony/") for n in names):
        det.frameworks.add("Symfony")
    if "phpstan/phpstan" in names:
        det.add_command("lint", ["vendor/bin/phpstan", "analyse"])
    if "laravel/pint" in names:
        det.add_command("format", ["vendor/bin/pint"])
    _db_from_names(names, det)


def detect_go_rust_dotnet(root: Path, det: Detection) -> None:
    if (root / "go.mod").exists():
        det.manifests.append("go.mod")
        det.build_systems.add("go")
        det.add_command("test", ["go", "test", "./..."])
        det.add_command("lint", ["go", "vet", "./..."])
        det.add_command("build", ["go", "build", "./..."])
    if (root / "Cargo.toml").exists():
        det.manifests.append("Cargo.toml")
        det.build_systems.add("cargo")
        det.add_command("test", ["cargo", "test"])
        det.add_command("lint", ["cargo", "clippy"])
        det.add_command("format", ["cargo", "fmt"])
        det.add_command("build", ["cargo", "build"])
    dotnet = sorted(p.name for p in [*root.glob("*.sln"), *root.glob("*.csproj")])
    if dotnet:
        det.manifests += dotnet
        det.build_systems.add("dotnet")
        det.add_command("test", ["dotnet", "test"])
        det.add_command("build", ["dotnet", "build"])
        det.add_command("format", ["dotnet", "format"])


# ===================================================================== containers / infra / CI
_COMPOSE_FILES: Final = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def detect_infrastructure(root: Path, files: list[WalkedFile], det: Detection) -> None:
    files = [f for f in files if not is_fixture_path(f.rel)]
    if any((root / f).exists() for f in ("Dockerfile", "dockerfile")) or any(
        f.path.name.startswith("Dockerfile") for f in files
    ):
        det.infrastructure.add("Docker")
    for name in _COMPOSE_FILES:
        compose = root / name
        if not compose.exists():
            continue
        det.infrastructure.add("Docker Compose")
        det.manifests.append(name)
        try:
            data = yaml.safe_load(_read_text(compose)) or {}
        except yaml.YAMLError:
            continue
        services = data.get("services") if isinstance(data, dict) else None
        if isinstance(services, dict):
            images = [str(s.get("image", "")) for s in services.values() if isinstance(s, dict)]
            _db_from_names([i.split(":")[0].split("/")[-1] for i in images if i], det)
    for f in files:
        name = f.path.name
        if name.endswith(".tf"):
            det.infrastructure.add("Terraform")
        elif name.endswith(".bicep"):
            det.infrastructure.add("Bicep")
        elif name == "Chart.yaml":
            det.infrastructure.add("Helm")
        elif name in ("Pulumi.yaml", "Pulumi.yml"):
            det.infrastructure.add("Pulumi")
        elif (
            name.endswith((".yaml", ".yml"))
            and f.size < 200_000
            and any(
                part in f.rel.lower()
                for part in ("k8s", "kubernetes", "deploy", "manifests", "kube")
            )
        ):
            head = _read_text(f.path, 4096)
            if "apiVersion:" in head and "kind:" in head:
                det.infrastructure.add("Kubernetes")
    ci = {
        ".github/workflows": "GitHub Actions",
        ".gitlab-ci.yml": "GitLab CI",
        "azure-pipelines.yml": "Azure Pipelines",
        "Jenkinsfile": "Jenkins",
        "bitbucket-pipelines.yml": "Bitbucket Pipelines",
        "cloudbuild.yaml": "Cloud Build",
    }
    for marker, name in ci.items():
        if (root / marker).exists():
            det.ci.add(name)
    terraform_providers = {
        'provider "azurerm"': "Azure",
        'provider "aws"': "AWS",
        'provider "google"': "GCP",
    }
    for f in files:
        if f.path.suffix == ".bicep":
            det.infrastructure.add("Azure")
        elif f.path.suffix == ".tf" and f.size < 200_000:
            text = _read_text(f.path, 20_000)
            for marker, provider in terraform_providers.items():
                if marker in text:
                    det.infrastructure.add(provider)


def detect(root: Path, files: list[WalkedFile]) -> Detection:
    det = Detection()
    for f in files:
        lang = LANGUAGE_BY_EXT.get(f.path.suffix.lower())
        if lang:
            det.languages[lang] += 1
    for detector in (detect_python, detect_node, detect_jvm, detect_php, detect_go_rust_dotnet):
        detector(root, det)
    detect_infrastructure(root, files, det)
    manifest_names = {
        "pyproject.toml",
        "package.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "go.mod",
        "Cargo.toml",
    }
    det.nested_manifests = sorted(
        f.rel
        for f in files
        if "/" in f.rel and f.path.name in manifest_names and not is_fixture_path(f.rel)
    )
    return det

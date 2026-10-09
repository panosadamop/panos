"""Golden-file tests for the Markdown/OpenAPI generators (M6-05).

Regenerate after an intentional template change with ``PANOS_UPDATE_GOLDEN=1`` and review
the diff before committing it.
"""

import os
from pathlib import Path

import pytest

from panos_core.config.settings import EstimationSettings
from panos_core.domain.requirements import Requirement, TraceLink, TraceRelation
from panos_core.domain.requirements import RequirementStatus as S
from panos_core.domain.requirements import RequirementType as T
from panos_core.requirements.artifacts import DOCUMENTS, ArtifactContext, render_all
from panos_core.requirements.estimation import estimate
from panos_core.requirements.skills import approved_facts, stack_of

pytestmark = pytest.mark.req("FR-008", "FR-029", "BR-028")

GOLDEN = Path(__file__).parent / "golden"
PID = "prj_" + "2" * 32


def item(
    key: str,
    t: T,
    title: str,
    parent: str | None = None,
    *,
    status: S = S.APPROVED,
    body: str = "",
    stale: bool = False,
    version: int = 1,
    **attrs: object,
) -> Requirement:
    return Requirement(
        project_id=PID,
        key=key,
        type=t,
        title=title,
        parent_key=parent,
        status=status,
        body=body,
        stale=stale,
        version=version,
        attributes=dict(attrs),
    )


def shop() -> ArtifactContext:
    items = [
        item("BR-001", T.BR, "Sell products online", body="Customers buy through a web shop."),
        item("FR-001", T.FR, "Checkout by card", "BR-001", version=2),
        item("FR-002", T.FR, "Catalog management", "BR-001", status=S.DRAFT),
        item("EPIC-001", T.EPIC, "Checkout", "FR-001"),
        item("FEAT-001", T.FEAT, "Card payment", "EPIC-001"),
        item(
            "US-001", T.US, "As a customer I can pay by card", "FEAT-001", points=5, priority="must"
        ),
        item("AC-US001-01", T.AC, "A valid card is charged once", "US-001"),
        item("AC-US001-02", T.AC, "A declined card shows an error", "US-001", stale=True),
        item("TC-001", T.TC, "Charge succeeds with a test card", "AC-US001-01"),
        item("US-002", T.US, "As an admin I can add a product", "FR-002", status=S.DRAFT),
        item("NFR-001", T.NFR, "Checkout responds fast", category="PERF", measure="p95 < 2 s"),
        item("RULE-001", T.RULE, "An order needs at least one item"),
        item("API-001", T.API, "Place an order", method="POST", path="/orders"),
        item(
            "DATA-001",
            T.DATA,
            "Order",
            fields=[{"name": "id", "type": "int"}, {"name": "total", "type": "decimal"}],
            relations=[{"target": "Product", "kind": "many-to-many", "label": "contains"}],
        ),
        item("DATA-002", T.DATA, "Product", fields=[{"name": "name", "type": "string"}]),
        item(
            "ADR-001",
            T.ADR,
            "Stack: Python / FastAPI",
            body="Team knows Python.",
            pack="python-fastapi",
            addons=["postgresql"],
        ),
        item("RISK-001", T.RISK, "Payment provider outage", mitigation="queue orders"),
        item("ASSUMPTION-001", T.ASSUMPTION, "Single currency (EUR)", status=S.DRAFT),
        item("DEPENDENCY-001", T.DEPENDENCY, "Card provider account", status=S.DRAFT),
    ]
    links = [
        TraceLink(
            project_id=PID, source_key="API-001", target="FR-001", relation=TraceRelation.REALIZES
        )
    ]
    return ArtifactContext(
        project="Web Shop",
        items=items,
        links=links,
        estimate=estimate(items, EstimationSettings()),
        stack=stack_of(approved_facts(items), "Web Shop"),
    )


@pytest.mark.parametrize("name", [*DOCUMENTS, "openapi.yaml"])
def test_documents_match_golden_files(name: str) -> None:
    rendered = render_all(shop())[name]
    path = GOLDEN / name
    if os.environ.get("PANOS_UPDATE_GOLDEN") == "1":
        GOLDEN.mkdir(exist_ok=True)
        path.write_bytes(rendered.encode("utf-8"))
    assert path.exists(), f"missing golden file {name}; run with PANOS_UPDATE_GOLDEN=1"
    assert rendered == path.read_bytes().decode("utf-8")

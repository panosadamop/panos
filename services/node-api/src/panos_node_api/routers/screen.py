"""Active-window analysis (ADR-025, US-013): capture on explicit request, local vision."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from panos_core.ai.ports import ProviderError
from panos_core.ai.privacy import PrivacyViolationError
from panos_core.ai.router import NoRouteError
from panos_core.projects.service import ProjectError
from panos_core.screen import ScreenError
from panos_node_api.routers.conversation import ai_error_response
from panos_node_api.routers.deps import LOCAL_USER, Container, raise_for_reason

router = APIRouter(prefix="/screen", tags=["screen"])


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="", max_length=2000)
    project: str | None = Field(default=None, description="project id or name (policy)")
    allow_cloud: bool = False  # explicit per-request consent (still needs a STANDARD project)


class AnalyzeView(BaseModel):
    text: str
    privacy: str
    window_title: str
    process: str | None
    width: int
    height: int
    provider: str
    model: str
    locality: str
    persisted: bool = False


@router.post("/analyze", response_model=AnalyzeView)
async def analyze(body: AnalyzeRequest, container: Container) -> Any:
    project = None
    if body.project:
        try:
            project = await container.projects.get(body.project)
        except ProjectError as exc:
            raise_for_reason(exc.reason, str(exc))
    try:
        result = await container.screen.analyze(
            body.question, project=project, allow_cloud=body.allow_cloud, actor=LOCAL_USER
        )
    except ScreenError as exc:
        raise_for_reason("unsupported", str(exc))
    except (NoRouteError, PrivacyViolationError, ProviderError) as exc:
        return ai_error_response(exc)
    return AnalyzeView(**result.__dict__ | {"privacy": result.privacy.value})

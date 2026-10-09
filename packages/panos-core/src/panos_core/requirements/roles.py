"""Business Analyst and Solution Architect roles for the requirements pipeline (AGENTS.md)."""

from pydantic import BaseModel, ConfigDict, Field

from panos_core.agents import AgentRole
from panos_core.ai.types import Complexity, TaskType


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Question(_Out):
    text: str
    why: str = ""


class AmbiguityReport(_Out):
    ambiguities: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list, max_length=12)


class SpecAC(_Out):
    title: str
    tests: list[str] = Field(default_factory=list)  # test case titles verifying it


class SpecStory(_Out):
    title: str
    body: str = ""
    points: int | None = None
    priority: str = "should"  # must | should | could | wont
    acceptance: list[SpecAC] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # question ids, e.g. ["q2"]


class SpecFeature(_Out):
    title: str
    stories: list[SpecStory] = Field(default_factory=list)


class SpecEpic(_Out):
    title: str
    body: str = ""
    features: list[SpecFeature] = Field(default_factory=list)


class SpecFR(_Out):
    ref: str  # short local reference used by APIs/data, e.g. "checkout"
    title: str
    body: str = ""
    epics: list[SpecEpic] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class SpecBR(_Out):
    title: str
    body: str = ""
    frs: list[SpecFR] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class SpecNFR(_Out):
    category: str = "GEN"  # PERF, SEC, AVAIL, USAB, ...
    title: str
    measure: str = ""


class SpecAPI(_Out):
    method: str = "GET"
    path: str
    title: str
    fr_ref: str | None = None


class SpecField(_Out):
    name: str
    type: str = "string"


class SpecRelation(_Out):
    target: str
    kind: str = "one-to-many"
    label: str = "has"


class SpecData(_Out):
    name: str
    body: str = ""
    fields: list[SpecField] = Field(default_factory=list)
    relations: list[SpecRelation] = Field(default_factory=list)
    fr_refs: list[str] = Field(default_factory=list)


class SpecRisk(_Out):
    title: str
    mitigation: str = ""


class SpecDraft(_Out):
    business: list[SpecBR] = Field(min_length=1)
    nfrs: list[SpecNFR] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    apis: list[SpecAPI] = Field(default_factory=list)
    data: list[SpecData] = Field(default_factory=list)
    risks: list[SpecRisk] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class StackProposal(_Out):
    pack: str
    addons: list[str] = Field(default_factory=list)
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


ANALYST_QUESTIONS = AgentRole(
    name="BusinessAnalyst",
    output_model=AmbiguityReport,
    task=TaskType.REQUIREMENTS,
    complexity=Complexity.MEDIUM,
    instructions=(
        "Read the project brief. List the ambiguities and missing information that would "
        "change scope, behaviour, data, users, integrations or quality targets, and ask at "
        "most 8 short, targeted questions that resolve them (most important first). Do not "
        "ask about things the brief already answers. Reply in the brief's language."
    ),
)

ANALYST_SPEC = AgentRole(
    name="BusinessAnalyst",
    output_model=SpecDraft,
    task=TaskType.REQUIREMENTS,
    complexity=Complexity.MEDIUM,
    max_output_tokens=8192,
    instructions=(
        "Turn the brief and the interview answers into a requirements specification: "
        "business requirements, each refined into functional requirements, each broken into "
        "epics, features and user stories ('As a <role> I can <goal> so that <value>') with "
        "story points (1,2,3,5,8,13), priority (must/should/could/wont), acceptance criteria "
        "and the test cases that verify them. Add NFRs with measurable targets, business "
        "rules, API operations (method + path, linked to the functional requirement 'ref'), "
        "data entities with fields and relations, risks with mitigations, assumptions and "
        "dependencies. For each item list the interview question ids it is based on in "
        "'sources'. Use only what the brief and answers support; record guesses as "
        "assumptions."
    ),
)

ARCHITECT = AgentRole(
    name="SolutionArchitect",
    output_model=StackProposal,
    task=TaskType.ARCHITECTURE,
    complexity=Complexity.MEDIUM,
    instructions=(
        "Propose the technology stack for this project from the available stack packs only "
        "(field 'pack' must be one of the listed pack ids; 'addons' from the listed add-on "
        "ids). Justify the choice against the requirements and list credible alternatives."
    ),
)

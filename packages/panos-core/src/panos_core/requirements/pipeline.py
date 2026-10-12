"""Brief → interview → specification → approved stack → scaffold → documents → skills.

FR-007 (project creation), US-030 (interview), US-021 (explicit stack honoured), M6-04..09.

* The interview is persisted after every step, so it survives restarts (AC-US030-02).
* Answers are provenance: every generated item lists the interview/question ids it is
  based on; skipped questions become explicit assumptions (AC-US030-03).
* A stack named in the brief is honoured without asking a model (US-021). Otherwise the
  Architect proposes one. Either way it is an ADR that must be **approved before
  scaffolding** (M6-06).
* Documents and skills are generated from the requirement store; skills use approved facts
  only and are versioned (US-033).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panos_core.agents import AgentTask, run_agent
from panos_core.ai.gateway import AIGateway, CallContext
from panos_core.audit import AuditService
from panos_core.config.settings import DocumentSettings, EstimationSettings
from panos_core.documents import find_fonts
from panos_core.domain import Project
from panos_core.domain.clock import utc_now
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.requirements import (
    GeneratedSkill,
    Interview,
    InterviewQuestion,
    InterviewStatus,
    Requirement,
    RequirementType,
    TraceRelation,
)
from panos_core.effort.service import EffortService, technology_from
from panos_core.persistence.unit_of_work import UnitOfWorkFactory
from panos_core.projects import ProjectService
from panos_core.repository import gitcli
from panos_core.requirements import packs as stack_packs
from panos_core.requirements.artifacts import ArtifactContext, render_all
from panos_core.requirements.estimation import Estimate, estimate
from panos_core.requirements.exports import FORMATS, export
from panos_core.requirements.roles import (
    ANALYST_QUESTIONS,
    ANALYST_SPEC,
    ARCHITECT,
    SpecDraft,
    StackProposal,
)
from panos_core.requirements.service import NewItem, RequirementsError, RequirementsService
from panos_core.requirements.skills import approved_facts, build_skills, render_skill_file, stack_of

T = RequirementType
DOCS_DIR = "docs/requirements"
EXPORTS_DIR = f"{DOCS_DIR}/exports"
MAX_QUESTIONS = 8


@dataclass
class ScaffoldReport:
    pack: str
    addons: list[str]
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    git_initialized: bool = False


class PipelineService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audit: AuditService,
        requirements: RequirementsService,
        projects: ProjectService,
        gateway: AIGateway,
        estimation: EstimationSettings,
        documents: DocumentSettings | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self.requirements = requirements
        self._projects = projects
        self._gateway = gateway
        self._estimation = estimation
        self._documents = documents or DocumentSettings()
        self._effort: EffortService | None = None

    def attach_effort(self, effort: "EffortService") -> None:
        """Approved calibrations and estimate baselines (ADR-031)."""
        self._effort = effort

    # ------------------------------------------------------------------ interview
    async def start(
        self,
        brief: str,
        *,
        project: Project | None = None,
        name: str | None = None,
        actor: str = "user",
    ) -> Interview:
        """Capture the brief (creating the project in the workspace if needed) and ask."""
        if project is None and not name:
            raise RequirementsError("invalid", "a new project needs a name")
        explicit, _ = stack_packs.detect_stack(brief)
        task = AgentTask(
            objective="Analyse this project brief for ambiguities.", inputs={"Brief": brief}
        )
        # Ask first: if no model is available, no half-created project is left behind.
        # A new project is LOCAL_ONLY (ADR-024), so the brief is handled as such.
        report = (await self._agent(project, ANALYST_QUESTIONS, task)).result
        if project is None:
            project = await self._projects.create(name or "", actor=actor)
        interview = Interview(
            project_id=project.id,
            brief=brief,
            explicit_stack=explicit,
            ambiguities=report.ambiguities,
            questions=[
                InterviewQuestion(id=f"q{n}", text=q.text, why=q.why)
                for n, q in enumerate(report.questions[:MAX_QUESTIONS], start=1)
            ],
        )
        if not interview.questions:
            interview.status = InterviewStatus.READY
        async with self._uow_factory() as uow:
            await uow.requirements.add_interview(interview)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="interview.started",
            action="interview.start",
            project_id=project.id,
            metadata={
                "interview_id": interview.id,
                "questions": len(interview.questions),
                "explicit_stack": explicit,
            },
        )
        return interview

    async def get_interview(self, interview_id: str) -> Interview:
        async with self._uow_factory() as uow:
            interview = await uow.requirements.get_interview(interview_id)
        if interview is None:
            raise RequirementsError("not_found", f"interview {interview_id!r} not found")
        return interview

    async def answer(
        self, interview_id: str, question_id: str, answer: str | None, *, actor: str = "user"
    ) -> Interview:
        """Record an answer (``None`` = skip). Persisted immediately (resumable)."""
        interview = await self.get_interview(interview_id)
        if interview.status == InterviewStatus.COMPLETED:
            raise RequirementsError("invalid_state", "the interview is already completed")
        question = next((q for q in interview.questions if q.id == question_id), None)
        if question is None:
            raise RequirementsError("not_found", f"question {question_id!r} not found")
        question.answer = answer.strip() if answer else None
        question.skipped = not answer
        question.answered_by, question.answered_at = actor, utc_now()
        if all(q.answer or q.skipped for q in interview.questions):
            interview.status = InterviewStatus.READY
        interview.updated_at = utc_now()
        async with self._uow_factory() as uow:
            await uow.requirements.update_interview(interview)
            await uow.commit()
        return interview

    async def complete(self, interview_id: str, *, actor: str = "user") -> list[Requirement]:
        """Generate the DRAFT specification and the stack decision from brief + answers."""
        interview = await self.get_interview(interview_id)
        if interview.status == InterviewStatus.COMPLETED:
            raise RequirementsError("invalid_state", "the interview is already completed")
        open_q = [q.id for q in interview.questions if not (q.answer or q.skipped)]
        if open_q:
            raise RequirementsError("invalid_state", f"unanswered questions: {open_q}")
        project = await self._projects.get(interview.project_id)
        qa = [
            {"id": q.id, "question": q.text, "answer": q.answer}
            for q in interview.questions
            if q.answer
        ]
        task = AgentTask(
            objective="Write the requirements specification for this project.",
            inputs={"Brief": interview.brief, "Interview answers": qa},
        )
        spec = (await self._agent(project, ANALYST_SPEC, task)).result
        skipped = [q for q in interview.questions if q.skipped]
        items = self._spec_items(spec, interview, skipped)
        created = await self.requirements.create_many(project.id, items, actor="agent:analyst")
        await self._link_spec(project.id, spec, items, created)
        created.append(await self._stack_decision(project, interview, created))
        interview.status, interview.updated_at = InterviewStatus.COMPLETED, utc_now()
        async with self._uow_factory() as uow:
            await uow.requirements.update_interview(interview)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="interview.completed",
            action="interview.complete",
            project_id=project.id,
            metadata={"interview_id": interview.id, "items": len(created)},
        )
        return created

    def _spec_items(
        self, spec: SpecDraft, interview: Interview, skipped: list[InterviewQuestion]
    ) -> list[NewItem]:
        def src(ids: list[str]) -> list[str]:
            known = {q.id for q in interview.questions}
            return [f"brief:{interview.id}"] + [
                f"answer:{interview.id}/{i}" for i in ids if i in known
            ]

        items: list[NewItem] = []
        for b, br in enumerate(spec.business):
            br_ref = f"br{b}"
            items.append(NewItem(T.BR, br.title, br.body, ref=br_ref, source=src(br.sources)))
            for fr in br.frs:
                fr_ref = f"fr:{fr.ref}"
                items.append(
                    NewItem(
                        T.FR,
                        fr.title,
                        fr.body,
                        parent_ref=br_ref,
                        ref=fr_ref,
                        source=src(fr.sources),
                    )
                )
                for e, epic in enumerate(fr.epics):
                    epic_ref = f"{fr_ref}/e{e}"
                    items.append(
                        NewItem(
                            T.EPIC,
                            epic.title,
                            epic.body,
                            parent_ref=fr_ref,
                            ref=epic_ref,
                            source=src([]),
                        )
                    )
                    for f, feat in enumerate(epic.features):
                        feat_ref = f"{epic_ref}/f{f}"
                        items.append(
                            NewItem(
                                T.FEAT,
                                feat.title,
                                parent_ref=epic_ref,
                                ref=feat_ref,
                                source=src([]),
                            )
                        )
                        for s, story in enumerate(feat.stories):
                            us_ref = f"{feat_ref}/s{s}"
                            attrs: dict[str, Any] = {"priority": story.priority}
                            if story.points:
                                attrs["points"] = story.points
                            items.append(
                                NewItem(
                                    T.US,
                                    story.title,
                                    story.body,
                                    parent_ref=feat_ref,
                                    ref=us_ref,
                                    attributes=attrs,
                                    source=src(story.sources),
                                )
                            )
                            for a, ac in enumerate(story.acceptance):
                                ac_ref = f"{us_ref}/a{a}"
                                items.append(
                                    NewItem(
                                        T.AC,
                                        ac.title,
                                        parent_ref=us_ref,
                                        ref=ac_ref,
                                        source=src(story.sources),
                                    )
                                )
                                items += [
                                    NewItem(T.TC, tc, parent_ref=ac_ref, source=src([]))
                                    for tc in ac.tests
                                ]
        items += [
            NewItem(
                T.NFR,
                n.title,
                attributes={"category": n.category.upper(), "measure": n.measure},
                source=src([]),
            )
            for n in spec.nfrs
        ]
        items += [NewItem(T.RULE, r, source=src([])) for r in spec.rules]
        items += [
            NewItem(
                T.API,
                a.title,
                ref=f"api:{n}",
                attributes={"method": a.method.upper(), "path": a.path},
                source=src([]),
            )
            for n, a in enumerate(spec.apis)
        ]
        items += [
            NewItem(
                T.DATA,
                d.name,
                d.body,
                ref=f"data:{n}",
                attributes={
                    "fields": [f.model_dump() for f in d.fields],
                    "relations": [r.model_dump() for r in d.relations],
                },
                source=src([]),
            )
            for n, d in enumerate(spec.data)
        ]
        items += [
            NewItem(T.RISK, r.title, attributes={"mitigation": r.mitigation}, source=src([]))
            for r in spec.risks
        ]
        items += [NewItem(T.ASSUMPTION, a, source=src([])) for a in spec.assumptions]
        items += [
            NewItem(
                T.ASSUMPTION,
                f"Unanswered: {q.text}",
                body="The interview question was skipped; confirm before implementation.",
                source=src([q.id]),
            )
            for q in skipped
        ]
        items += [NewItem(T.DEPENDENCY, d, source=src([])) for d in spec.dependencies]
        return items

    async def _link_spec(
        self, project_id: str, spec: SpecDraft, items: list[NewItem], created: list[Requirement]
    ) -> None:
        by_ref = {
            spec_item.ref: req.key
            for spec_item, req in zip(items, created, strict=True)
            if spec_item.ref
        }
        for n, api in enumerate(spec.apis):
            target = by_ref.get(f"fr:{api.fr_ref}") if api.fr_ref else None
            if target:
                await self.requirements.link(
                    project_id,
                    by_ref[f"api:{n}"],
                    target,
                    TraceRelation.REALIZES,
                    actor="agent:analyst",
                )
        for n, data in enumerate(spec.data):
            for ref in data.fr_refs:
                target = by_ref.get(f"fr:{ref}")
                if target:
                    await self.requirements.link(
                        project_id,
                        by_ref[f"data:{n}"],
                        target,
                        TraceRelation.REALIZES,
                        actor="agent:analyst",
                    )

    async def _stack_decision(
        self, project: Project, interview: Interview, created: list[Requirement]
    ) -> Requirement:
        explicit, mentioned_addons = stack_packs.detect_stack(interview.brief)
        if explicit:
            pack = stack_packs.packs()[explicit]
            chosen = list(dict.fromkeys([*mentioned_addons, *pack.default_addons]))
            return await self.requirements.create(
                project.id,
                T.ADR,
                f"Stack: {pack.name}",
                body="Requested explicitly in the brief; honoured as stated (US-021).",
                attributes={"pack": pack.id, "addons": chosen, "explicit": True},
                source=[f"brief:{interview.id}"],
                actor="agent:architect",
            )
        catalogue = {
            "packs": [
                {"id": p.id, "name": p.name, "frameworks": list(p.frameworks)}
                for p in stack_packs.packs().values()
            ],
            "addons": [{"id": a.id, "name": a.name} for a in stack_packs.addons().values()],
        }
        summary = [f"{r.key}: {r.title}" for r in created if r.type in (T.BR, T.FR, T.NFR, T.DATA)][
            :80
        ]
        task = AgentTask(
            objective="Propose the technology stack.",
            inputs={
                "Available stacks": catalogue,
                "Requirements": summary,
                "Brief": interview.brief,
            },
        )
        proposal: StackProposal = (await self._agent(project, ARCHITECT, task)).result
        if proposal.pack not in stack_packs.packs():
            raise RequirementsError(
                "invalid", f"the architect proposed an unknown stack {proposal.pack!r}"
            )
        chosen = [a for a in proposal.addons if a in stack_packs.addons()]
        return await self.requirements.create(
            project.id,
            T.ADR,
            f"Stack: {stack_packs.packs()[proposal.pack].name}",
            body=proposal.rationale
            + (
                "\n\nAlternatives: " + "; ".join(proposal.alternatives)
                if proposal.alternatives
                else ""
            ),
            attributes={"pack": proposal.pack, "addons": chosen, "explicit": False},
            source=[f"brief:{interview.id}"],
            actor="agent:architect",
        )

    async def _agent(self, project: Project | None, role: Any, task: AgentTask) -> Any:
        ctx = CallContext(
            actor=f"agent:{role.name.lower()}", project_id=project.id if project else None
        )
        privacy = project.privacy_mode if project else PrivacyMode.LOCAL_ONLY
        return await run_agent(role, task, gateway=self._gateway, ctx=ctx, privacy=privacy)

    # ------------------------------------------------------------------ outputs
    async def technology(self, project: Project) -> str:
        items = await self.requirements.list_latest(project.id)
        stack = stack_of(approved_facts(items), project.name)
        async with self._uow_factory() as uow:
            snapshot = await uow.snapshots.latest(project.id)
        return technology_from(stack["pack"] if stack else None, snapshot)

    async def estimate(
        self, project: Project, *, record: bool = False, actor: str = "user"
    ) -> Estimate:
        """``record``: store the result as a baseline for estimate-vs-actual (explicit runs)."""
        items = await self.requirements.list_latest(project.id)
        async with self._uow_factory() as uow:
            interviews = await uow.requirements.interviews(project.id)
        open_questions = sum(1 for i in interviews for q in i.questions if q.skipped)
        hpp, calibration, technology = None, None, "unknown"
        if self._effort is not None:
            technology = await self.technology(project)
            hpp, calibration = await self._effort.hours_per_point(technology)
        note = (
            f"calibrated from {calibration.stories} finished stories ({calibration.scope}), "
            f"approved by {calibration.decided_by} on {calibration.decided_at:%Y-%m-%d}"
            if calibration and calibration.decided_at
            else None
        )
        result = estimate(
            items,
            self._estimation,
            open_questions=open_questions,
            hours_per_point=hpp,
            calibration_note=note,
        )
        if record and self._effort is not None and result.stories:
            await self._effort.record_baseline(
                project,
                result,
                technology=technology,
                hours_per_point=hpp or self._estimation.hours_per_point,
                calibration=calibration,
                actor=actor,
            )
        return result

    async def _artifact_context(self, project: Project) -> ArtifactContext:
        items = await self.requirements.list_latest(project.id)
        return ArtifactContext(
            project=project.name,
            items=items,
            links=list(await self.requirements.links(project.id)),
            estimate=await self.estimate(project),
            stack=stack_of(approved_facts(items), project.name),
        )

    async def export(
        self, project: Project, formats: list[str] | None = None, *, actor: str = "user"
    ) -> list[str]:
        """Write DOCX/PDF/XLSX/PPTX exports to ``docs/requirements/exports`` (FR-029)."""
        ctx = await self._artifact_context(project)
        wanted = formats or list(FORMATS)
        fonts = find_fonts(self._documents.pdf_font_dir) if "pdf" in wanted else None
        files = export(ctx, wanted, fonts=fonts)
        root = Path(project.path) / EXPORTS_DIR
        root.mkdir(parents=True, exist_ok=True)
        written = []
        for name, content in files.items():
            (root / name).write_bytes(content)
            written.append(f"{EXPORTS_DIR}/{name}")
        await self._audit.record(
            actor=actor,
            event_type="requirements.exported",
            action="requirements.export",
            project_id=project.id,
            metadata={"files": written},
        )
        return written

    async def documents(self, project: Project, *, actor: str = "user") -> list[str]:
        ctx = await self._artifact_context(project)
        root = Path(project.path) / DOCS_DIR
        root.mkdir(parents=True, exist_ok=True)
        written = []
        for name, content in render_all(ctx).items():
            (root / name).write_bytes(content.encode("utf-8"))
            written.append(f"{DOCS_DIR}/{name}")
        await self._audit.record(
            actor=actor,
            event_type="requirements.documents",
            action="requirements.generate_documents",
            project_id=project.id,
            metadata={"files": written},
        )
        return written

    async def skills(self, project: Project, *, actor: str = "user") -> list[dict[str, Any]]:
        items = await self.requirements.list_latest(project.id)
        out = []
        for draft in build_skills(project.name, items):
            rel = f".claude/skills/{draft.name}/SKILL.md"
            async with self._uow_factory() as uow:
                current = await uow.requirements.get_skill(project.id, draft.name)
                changed = current is None or current.facts_hash != draft.facts_hash
                missing = not (Path(project.path) / rel).exists()
                if changed or missing:
                    version = (
                        current.version + 1
                        if current and changed
                        else current.version
                        if current
                        else 1
                    )
                    skill = GeneratedSkill(
                        id=current.id
                        if current
                        else GeneratedSkill(
                            project_id=project.id, name=draft.name, facts_hash="0" * 64, path=rel
                        ).id,
                        project_id=project.id,
                        name=draft.name,
                        version=version,
                        facts_hash=draft.facts_hash,
                        source_keys=draft.source_keys,
                        path=rel,
                    )
                    target = Path(project.path) / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(render_skill_file(draft, version).encode("utf-8"))
                    await uow.requirements.save_skill(skill)
                    await uow.commit()
                    out.append(
                        {
                            "name": draft.name,
                            "version": version,
                            "path": rel,
                            "updated": True,
                            "sources": draft.source_keys,
                        }
                    )
                else:
                    assert current is not None  # noqa: S101 - changed is False only then
                    out.append(
                        {
                            "name": draft.name,
                            "version": current.version,
                            "path": rel,
                            "updated": False,
                            "sources": current.source_keys,
                        }
                    )
        await self._audit.record(
            actor=actor,
            event_type="skills.generated",
            action="skills.generate",
            project_id=project.id,
            metadata={"skills": out},
        )
        return out

    async def scaffold(
        self, project: Project, *, actor: str = "user", git_init: bool = True
    ) -> ScaffoldReport:
        """Render the **approved** stack into the project, then documents and skills."""
        items = await self.requirements.list_latest(project.id)
        stack = stack_of(approved_facts(items), project.name)
        if stack is None:
            pending = [i.key for i in items if i.type == T.ADR and i.attributes.get("pack")]
            raise RequirementsError(
                "not_approved",
                "the stack decision must be approved before scaffolding"
                + (f" (approve {', '.join(pending)})" if pending else ""),
            )
        root = Path(project.path)
        result = stack_packs.scaffold(
            root, stack["pack"], stack["addons"], name=project.name, description=project.name
        )
        report = ScaffoldReport(
            pack=stack["pack"],
            addons=stack["addons"],
            created=result.created,
            skipped=result.skipped,
        )
        if git_init and not (root / ".git").exists() and await _git_init(root):
            report.git_initialized = True
        report.documents = await self.documents(project, actor=actor)
        report.skills = await self.skills(project, actor=actor)
        await self._audit.record(
            actor=actor,
            event_type="project.scaffolded",
            action="project.scaffold",
            project_id=project.id,
            metadata={
                "pack": stack["pack"],
                "addons": stack["addons"],
                "created": len(result.created),
                "skipped": result.skipped,
            },
        )
        return report


async def _git_init(root: Path) -> bool:
    """A local repository for the new project (no commit: commits need approval)."""
    try:
        await gitcli.run_git(root, "init", "-q")
    except gitcli.GitError:
        return False
    return True

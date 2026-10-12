"""Rule-based intent fast path (NFR-PERF-002: simple local commands need no model at all).

Recognised commands (English and Greek) are answered locally in milliseconds. Requests
that look like work for an autonomous job are tagged ``task`` (the job runner arrives in
M5); everything else is ``chat`` and goes to the model router.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

IntentKind = Literal[
    "screen",
    "list_projects",
    "open_project",
    "open_app",
    "list_apps",
    "status",
    "inbox",
    "agenda",
    "research",
    "task",
    "chat",
]


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    target: str | None = None


_PATTERNS: Final[tuple[tuple[IntentKind, re.Pattern[str]], ...]] = (
    (
        "inbox",
        re.compile(
            r"^(?:(?:check|show|read)(?: me)?(?: my)? (?:e-?mails?|inbox|mail)"
            r"|(?:what(?:'s| is) in|anything (?:new )?in) my (?:inbox|mail)"
            r"|(?:do i have|have i got|any) (?:any )?(?:new |unread )?(?:e-?mails?|mail)"
            r"|(?:δείξε|έλεγξε|διάβασε)(?: μου)?(?: τα)? (?:e-?mails?|μηνύματα|εισερχόμενα|mail)"
            r"(?: μου)?"
            r"|(?:έχω|ήρθαν) (?:κάποια |νέα |καινούρια |αδιάβαστα )*(?:e-?mails?|μηνύματα|mail))"
            r"[?;;.]?$",
            re.I,
        ),
    ),
    (
        "agenda",
        re.compile(
            r"^(?:what(?:'s| is| do i have)(?: on)?(?: my)?(?: (?:calendar|schedule|agenda))?"
            r" (?P<target>today|tomorrow)|what(?:'s| is) on my (?:calendar|schedule|agenda)"
            r"|(?:show )?my (?:meetings|agenda|schedule|calendar)"
            r"(?: for)?(?: (?P<t2>today|tomorrow))?"
            r"|τι (?:έχω|ραντεβού έχω|συναντήσεις έχω|έχω στο ημερολόγιο) (?P<t3>σήμερα|αύριο)"
            r"|(?:δείξε )?(?:μου )?(?:το )?(?:πρόγραμμ[αά]|ημερολόγι[οό]) μου"
            r"(?: (?:για )?(?P<t4>σήμερα|αύριο))?)"
            r"[?;;.]?$",
            re.I,
        ),
    ),
    (
        "research",
        re.compile(
            r"^(?:research|look up|search the web for|find (?:sources|information) (?:on|about)"
            r"|ψάξε(?: στο (?:διαδίκτυο|internet|ίντερνετ))? (?:για|πληροφορίες για)"
            r"|κάνε (?:μια )?έρευνα (?:για|σχετικά με)|βρες πληροφορίες (?:για|σχετικά με))"
            r" (?P<target>.+?)[?;;.]?$",
            re.I,
        ),
    ),
    (
        "screen",
        re.compile(
            r"^(?:what(?:'s| is) (?:on|wrong (?:on|with)) (?:my|the) screen|look at (?:my|the) "
            r"screen|(?:check|read) (?:this|the) error(?: on (?:my|the) screen)?|τι (?:βλέπεις|"
            r"δείχνει) (?:στην|η) οθόνη|κοίτα (?:την|στην) οθόνη|τι λάθος (?:βλέπεις|έχει) "
            r"(?:στην οθόνη)?)[?;\u037e]?$",
            re.I,
        ),
    ),
    (
        "list_projects",
        re.compile(
            r"^(?:list|show)(?: (?:me|all|my))? projects?\??$|^(?:δείξε|λίστα|ποια είναι)"
            r"(?: (?:μου|τα|όλα))*(?: τα)? (?:projects?|έργα)\??$",
            re.I,
        ),
    ),
    (
        "list_apps",
        re.compile(
            r"^(?:list|show)(?: (?:me|all|my|installed))* (?:apps?|applications?|ides?)\??$"
            r"|^(?:δείξε|λίστα)(?: (?:μου|τις|τα))* (?:εφαρμογές|ide)\??$",
            re.I,
        ),
    ),
    ("status", re.compile(r"^(?:status|are you (?:there|ok)\??|κατάσταση|είσαι εκεί\??)$", re.I)),
    (
        "open_project",
        re.compile(
            r"^(?:open|άνοιξε)(?: (?:the|το))? (?:project|έργο) (?P<target>[\w .\-]+?)\.?$", re.I
        ),
    ),
    (
        "open_app",
        re.compile(
            r"^(?:open|launch|start|άνοιξε|ξεκίνα)(?: (?:the|το|τον))? (?P<target>[\w .\-]+?)\.?$",
            re.I,
        ),
    ),
)
_TASK_RE: Final = re.compile(
    r"^(?:implement|create|build|add|fix|refactor|write|generate|scaffold|migrate|deploy|"
    r"υλοποίησε|φτιάξε|δημιούργησε|πρόσθεσε|διόρθωσε|γράψε|μετάφερε)\b",
    re.I,
)


def classify_intent(text: str) -> Intent:
    cleaned = " ".join(text.strip().split())
    cleaned = re.sub(r"^(?:panos|πάνο|πάνος)[,:]?\s+", "", cleaned, flags=re.I)
    for kind, pattern in _PATTERNS:
        m = pattern.match(cleaned)
        if m:
            groups = m.groupdict()
            target = next((v for k, v in groups.items() if v and k[0] == "t"), None)
            if kind == "agenda":
                target = (
                    "tomorrow" if target and target.lower() in ("tomorrow", "αύριο") else "today"
                )
            return Intent(kind, target.strip() if target else None)
    if _TASK_RE.match(cleaned):
        return Intent("task")
    return Intent("chat")

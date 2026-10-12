"""A small Markdown reader for the documents Panos generates (FR-029).

It covers the subset the requirement templates emit: ATX headings, paragraphs, nested
bullet and numbered lists, pipe tables, fenced code blocks, block quotes and horizontal
rules, with inline ``**bold**``, ``*italic*``/``_italic_`` and ```code```. Anything else is
kept as plain paragraph text, so an export never drops content.
"""

import re
from dataclasses import dataclass, field
from typing import Final

_HEADING: Final = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET: Final = re.compile(r"^(\s*)(?:[-*+]|(\d+)[.)])\s+(.*)$")
_FENCE: Final = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
_RULE: Final = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP: Final = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE: Final = re.compile(
    r"(\*\*.+?\*?\*\*(?!\*)"  # bold (may end in an italic: ***)
    r"|`[^`]+`"  # code
    r"|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*])"  # *italic*
    r"|(?<!\w)_[^_\s][^_]*?_(?!\w))"  # _italic_ (not snake_case)
)


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class ListItem:
    depth: int  # 0 = top level
    text: str
    ordered: bool = False
    number: int | None = None


@dataclass(frozen=True)
class ListBlock:
    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Code:
    text: str
    language: str = ""


@dataclass(frozen=True)
class Quote:
    text: str


@dataclass(frozen=True)
class Rule:
    pass


Block = Heading | Paragraph | ListBlock | Table | Code | Quote | Rule


@dataclass
class _State:
    blocks: list[Block] = field(default_factory=list)
    paragraph: list[str] = field(default_factory=list)
    items: list[ListItem] = field(default_factory=list)
    quote: list[str] = field(default_factory=list)

    def flush(self) -> None:
        if self.paragraph:
            self.blocks.append(Paragraph(" ".join(s.strip() for s in self.paragraph)))
            self.paragraph = []
        if self.items:
            self.blocks.append(ListBlock(tuple(self.items)))
            self.items = []
        if self.quote:
            self.blocks.append(Quote(" ".join(s.strip() for s in self.quote)))
            self.quote = []


def _cells(line: str) -> tuple[str, ...]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return tuple(c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line))


def parse(text: str) -> list[Block]:
    """Parse Markdown into blocks."""
    lines = text.replace("\r\n", "\n").split("\n")
    st = _State()
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            st.flush()
            marker, language = fence.group(1), fence.group(2)
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            st.blocks.append(Code("\n".join(body), language))
            i += 1
            continue
        if not line.strip():
            st.flush()
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            st.flush()
            st.blocks.append(Heading(len(heading.group(1)), heading.group(2)))
            i += 1
            continue
        if _RULE.match(line):
            st.flush()
            st.blocks.append(Rule())
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            st.flush()
            header = _cells(line)
            rows: list[tuple[str, ...]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _cells(lines[i])
                # Pad/trim so every row has the header's width.
                rows.append((cells + ("",) * len(header))[: len(header)])
                i += 1
            st.blocks.append(Table(header, tuple(rows)))
            continue
        if line.lstrip().startswith(">"):
            if st.paragraph or st.items:
                st.flush()
            st.quote.append(line.lstrip()[1:])
            i += 1
            continue
        bullet = _BULLET.match(line)
        if bullet:
            if st.paragraph or st.quote:
                st.flush()
            indent = len(bullet.group(1).replace("\t", "    "))
            number = bullet.group(2)
            st.items.append(
                ListItem(
                    depth=indent // 2,
                    text=bullet.group(3).strip(),
                    ordered=number is not None,
                    number=int(number) if number else None,
                )
            )
            i += 1
            continue
        if st.items and line.startswith(" "):
            # Continuation of the previous list item.
            last = st.items[-1]
            st.items[-1] = ListItem(
                last.depth, f"{last.text} {line.strip()}", last.ordered, last.number
            )
            i += 1
            continue
        if st.items or st.quote:
            st.flush()
        st.paragraph.append(line)
        i += 1
    st.flush()
    return st.blocks


def runs(text: str) -> list[Run]:
    """Split inline Markdown into styled runs."""
    out: list[Run] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.extend(
                Run(r.text, bold=True, italic=r.italic, code=r.code) for r in runs(part[2:-2])
            )
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(Run(part[1:-1], code=True))
        elif len(part) > 2 and part[0] == part[-1] and part[0] in "*_":
            out.append(Run(part[1:-1], italic=True))
        else:
            out.append(Run(part))
    return out


def plain(text: str) -> str:
    """Inline Markdown without its markers (for table cells, titles and plain formats)."""
    return "".join(r.text for r in runs(text))

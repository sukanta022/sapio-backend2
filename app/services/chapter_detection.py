import re
from dataclasses import dataclass


MAX_DETECTED_CHAPTERS = 500

CHAPTER_PATTERN = re.compile(
    r"^chapter\s+"
    r"(?P<number>"
    r"\d{1,4}"
    r"|[IVXLCDM]{1,12}"
    r"|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|thirteen|fourteen|fifteen"
    r"|sixteen|seventeen|eighteen|nineteen|twenty"
    r")"
    r"\b"
    r"(?:\s*[:.\-–—]\s*|\s+)?"
    r"(?P<title>.*)$",
    re.IGNORECASE,
)


class ChapterDetectionError(Exception):
    """Chapter detection could not be completed."""


@dataclass(frozen=True)
class DetectedChapter:
    chapter_number: int
    title: str
    content: str
    start_position: int
    end_position: int


def _trim_range(
    text: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1

    while end > start and text[end - 1].isspace():
        end -= 1

    return start, end


def _find_headings(text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []

    offset = 0

    for line in text.splitlines(keepends=True):
        stripped = line.strip()

        if stripped and len(stripped) <= 120:
            match = CHAPTER_PATTERN.fullmatch(stripped)

            if match:
                heading_start = (
                    offset
                    + len(line)
                    - len(line.lstrip())
                )

                headings.append(
                    (heading_start, stripped)
                )

        offset += len(line)

    return headings


def detect_chapters(
    cleaned_text: str,
) -> list[DetectedChapter]:
    if not cleaned_text or not cleaned_text.strip():
        raise ChapterDetectionError(
            "Cleaned document text is empty."
        )

    headings = _find_headings(cleaned_text)

    if len(headings) > MAX_DETECTED_CHAPTERS:
        raise ChapterDetectionError(
            "Too many chapter headings were detected."
        )

    sections: list[tuple[str, int, int]] = []

    if not headings:
        sections.append(
            ("Full Document", 0, len(cleaned_text))
        )

    else:
        first_start = headings[0][0]

        # Preserve meaningful content before Chapter 1.
        intro_start, intro_end = _trim_range(
            cleaned_text,
            0,
            first_start,
        )

        if intro_start < intro_end:
            sections.append(
                ("Introduction", intro_start, intro_end)
            )

        for index, (start, heading) in enumerate(
            headings
        ):
            if index + 1 < len(headings):
                end = headings[index + 1][0]
            else:
                end = len(cleaned_text)

            sections.append(
                (heading, start, end)
            )

    if len(sections) > MAX_DETECTED_CHAPTERS:
        raise ChapterDetectionError(
            "Document exceeds the chapter limit."
        )

    chapters: list[DetectedChapter] = []

    for title, section_start, section_end in sections:
        start, end = _trim_range(
            cleaned_text,
            section_start,
            section_end,
        )

        if start >= end:
            continue

        chapters.append(
            DetectedChapter(
                chapter_number=len(chapters) + 1,
                title=title[:255],
                content=cleaned_text[start:end],
                start_position=start,
                end_position=end,
            )
        )

    if not chapters:
        raise ChapterDetectionError(
            "No chapter content could be created."
        )

    return chapters

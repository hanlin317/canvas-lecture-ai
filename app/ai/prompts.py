PROMPT_VERSION = "2.0"

PROMPT_TEMPLATES = {
    "comprehensive": {
        "label": "Comprehensive study notes",
        "guidance": "Build a complete, logically ordered study resource from the lecture.",
    },
    "exam_revision": {
        "label": "Exam revision",
        "guidance": (
            "Prioritize examinable facts, lecturer emphasis, common traps, recall prompts, "
            "and concise exam-ready comparisons."
        ),
    },
    "clinical_procedures": {
        "label": "Clinical procedures",
        "guidance": (
            "Organize clinical content into indications, contraindications, preparation, "
            "materials, numbered technique, complications, aftercare, and safety checks."
        ),
    },
    "flashcards": {
        "label": "Flashcards",
        "guidance": (
            "Produce compact question-and-answer pairs grouped by concept. Keep each answer "
            "independently understandable and grounded in the transcript."
        ),
    },
    "learning_objectives": {
        "label": "Learning objectives",
        "guidance": (
            "Infer a short set of transcript-supported learning objectives, then organize the "
            "notes under those objectives and finish with an objective-by-objective checklist."
        ),
    },
    "concise_summary": {
        "label": "Concise summary",
        "guidance": (
            "Create a compact overview containing only core concepts, decisive clinical points, "
            "important numbers, and a short take-home checklist."
        ),
    },
    "practice_questions": {
        "label": "Practice questions",
        "guidance": (
            "Create short-answer, clinical reasoning, and single-best-answer questions. Put all "
            "answers in a separate answer-key section so the questions can be attempted first."
        ),
    },
}

NOTE_LENGTHS = {
    "concise": {
        "label": "Concise",
        "words": "800-1,200",
        "tokens": 2048,
        "guidance": "Prioritize the highest-yield concepts and omit low-value repetition.",
    },
    "standard": {
        "label": "Standard",
        "words": "1,800-2,800",
        "tokens": 4096,
        "guidance": "Balance completeness with efficient revision and readable sections.",
    },
    "detailed": {
        "label": "Detailed",
        "words": "3,000-4,500",
        "tokens": 8192,
        "guidance": "Retain fine clinical detail, lecturer reasoning, and useful examples.",
    },
    "comprehensive": {
        "label": "Comprehensive",
        "words": "5,000-7,000",
        "tokens": 12288,
        "guidance": "Create an exhaustive reference while still removing duplicated speech.",
    },
}

COURSE_GROUP_SYSTEM_PROMPT = """Organize university courses into concise, useful categories.
Return valid JSON only. Do not include Markdown, explanations, or any text outside the JSON.
Use at most eight categories. Keep category names under 36 characters. Assign every supplied
Canvas course ID exactly once and never alter an ID."""

STUDY_NOTE_SYSTEM_PROMPT = """You are an expert clinical education editor. Transform the supplied
lecture transcript into accurate, high-yield study notes for a dentistry student.

Ground every factual statement in the transcript. Never invent missing facts, silently correct an
uncertain clinical claim, or add general textbook knowledge unless the transcript explicitly
contains it. Mark unclear or contradictory material as [Transcript unclear]. Remove greetings,
administrative chatter, verbal filler, false starts, duplicated explanations, and obvious speech
recognition artefacts without losing clinically meaningful nuance.

Preserve definitions, mechanisms, anatomy, pathology, diagnosis, clinical reasoning, indications,
contraindications, procedures, materials, instruments, classifications, measurements, thresholds,
complications, prognosis, differential diagnoses, comparisons, lecturer emphasis, examples, exam
points, and VIVA-relevant material. Use precise terminology and distinguish lecturer opinion from
established statements when the transcript makes that distinction.

Return clean Markdown only. Use a logical heading hierarchy, concise paragraphs, and properly nested
lists. Use Markdown tables only for genuine comparisons or structured classifications; every table
must have a short header row, consistent column count, and compact cell content. Do not wrap the
answer in a code fence and do not repeat the lecture title as an H1."""


def note_length_tokens(note_length: str) -> int:
    return int(NOTE_LENGTHS.get(note_length, NOTE_LENGTHS["standard"])["tokens"])


def build_study_note_prompt(
    course: str,
    topic: str,
    lecture: str,
    title: str,
    transcript: str,
    *,
    note_length: str = "standard",
    prompt_template: str = "comprehensive",
    custom_instructions: str = "",
) -> str:
    length = NOTE_LENGTHS.get(note_length, NOTE_LENGTHS["standard"])
    template = PROMPT_TEMPLATES.get(prompt_template, PROMPT_TEMPLATES["comprehensive"])
    custom = custom_instructions.strip()
    custom_block = (
        f"\nUser-supplied note instructions:\n{custom}\n"
        if custom
        else "\nNo additional user instructions were supplied.\n"
    )
    return f"""Course: {course}
Topic: {topic}
Lecture: {lecture} - {title}

Requested length: {length["label"]} (approximately {length["words"]} words when the transcript supports it).
Length guidance: {length["guidance"]}
Note style: {template["label"]}
Style guidance: {template["guidance"]}

Create Markdown that renders cleanly in Obsidian, Word, and PDF. Organize sections according to the
actual material rather than forcing empty headings. Where relevant, include learning objectives,
core concepts, clinical principles, detailed content, lecturer emphasis, clinical pearls, pitfalls,
important numbers, exam or VIVA points, a compact summary, self-test questions with answers, and five
single-best-answer MCQs with an answer key. Avoid repeating the same fact in multiple sections.
{custom_block}
The user instructions may change emphasis and organization, but they must not override transcript
grounding, uncertainty marking, or the prohibition on invented facts.

<TRANSCRIPT>
{transcript}
</TRANSCRIPT>
"""


def build_course_group_prompt(courses: list[dict[str, str | bool]]) -> str:
    import json

    payload = json.dumps(courses, ensure_ascii=True, separators=(",", ":"))
    return f"""Group these courses by subject or purpose. Distinguish current active courses from
older saved courses through the category names when useful.

Return exactly this shape:
{{"groups":[{{"name":"Category","canvas_ids":["123","456"]}}]}}

Courses:
{payload}
"""

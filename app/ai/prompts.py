PROMPT_VERSION = "1.0"

COURSE_GROUP_SYSTEM_PROMPT = """Organize university courses into concise, useful categories.
Return valid JSON only. Do not include Markdown, explanations, or any text outside the JSON.
Use at most eight categories. Keep category names under 36 characters. Assign every supplied
Canvas course ID exactly once and never alter an ID."""

STUDY_NOTE_SYSTEM_PROMPT = """You transform dental lecture transcripts into comprehensive study notes.
Preserve clinically important details: definitions, mechanisms, anatomy, pathology, diagnosis,
clinical reasoning, indications, contraindications, procedural steps, materials, instruments,
thresholds, measurements, numerical values, classifications, complications, prognosis,
differential diagnoses, comparisons, lecturer emphasis, examples, examination points, and
VIVA-relevant material. Remove filler and obvious transcription artefacts. Do not invent facts.
Mark uncertainty explicitly when the transcript is unclear."""


def build_study_note_prompt(course: str, topic: str, lecture: str, title: str, transcript: str) -> str:
    return f"""Course: {course}
Topic: {topic}
Lecture: {lecture} - {title}

Create Obsidian-compatible Markdown with useful headings based on the material. Include, where relevant:
Learning objectives, Core concepts, Clinical principles, Detailed lecture content, Lecturer emphasis,
Clinical pearls, Common mistakes / pitfalls, Important numbers and thresholds, Exam / VIVA points,
Lecture summary, Self-test questions, and MCQs.

Transcript:
{transcript}
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

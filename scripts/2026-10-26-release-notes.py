from __future__ import annotations

import argparse
import re
from pathlib import Path

RELEASE_HEADING = re.compile(r"^## \[(?P<version>[^]]+)](?: - (?P<date>\d{4}-\d{2}-\d{2}))?$")


def extract_release_notes(changelog: str, version: str) -> str:
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = RELEASE_HEADING.match(line.strip())
        if match and match.group("version") == version:
            if not match.group("date"):
                raise ValueError(f"Changelog entry {version} must include a release date.")
            start = index + 1
            break
    if start is None:
        raise ValueError(f"Changelog does not contain a release entry for {version}.")

    end = len(lines)
    for index in range(start, len(lines)):
        if RELEASE_HEADING.match(lines[index].strip()):
            end = index
            break
    notes = "\n".join(lines[start:end]).strip()
    if not notes or not re.search(r"^### ", notes, flags=re.MULTILINE):
        raise ValueError(f"Changelog entry {version} must contain at least one category.")
    return notes


def release_notes(
    changelog: str,
    version: str,
    repository: str,
    previous_version: str | None,
) -> str:
    notes = extract_release_notes(changelog, version)
    if previous_version:
        comparison = (
            f"https://github.com/{repository}/compare/v{previous_version}...v{version}"
        )
        notes += f"\n\n**Full changes:** [{previous_version}...{version}]({comparison})"
    notes += (
        "\n\n### Installation\n\n"
        "On macOS, choose the arm64 DMG for Apple Silicon or the x64 DMG for an Intel Mac, open "
        "it, and drag Canvas Lecture AI into Applications. On Windows, choose the x64 installer "
        "for Intel/AMD computers or the arm64 installer for Windows 11 ARM devices.\n\n"
        "### Updating\n\n"
        "Existing packaged installations can install this release through the signed in-app "
        "updater. Source installations should update with Git."
    )
    return notes + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract validated GitHub release notes.")
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--previous-version")
    parser.add_argument("--output", required=True, help="Output path, or - for standard output")
    args = parser.parse_args()

    try:
        notes = release_notes(
            args.changelog.read_text(encoding="utf-8"),
            args.version,
            args.repository,
            args.previous_version,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.output == "-":
        print(notes, end="")
    else:
        Path(args.output).write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()

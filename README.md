# Canvas Lecture AI

**Download Canvas and Echo360 lectures, clean transcripts, and create study notes.**

Canvas Lecture AI is a local macOS and Windows application. It discovers Echo360
recordings from Canvas, preserves the original folder names, downloads available
videos and slides, prepares transcripts, and optionally creates AI study material
for Word, PDF, Markdown, Obsidian, NotebookLM, or Anki.

The interface runs only on your computer at `http://127.0.0.1:8017`. Canvas
passwords are entered in a visible browser and are not read by the application.

> [!IMPORTANT]
> Use Canvas Lecture AI only with accounts and educational material you are
> authorized to access. Follow your institution's policies and do not redistribute
> lecture material without permission.

This is an independent, unofficial project. It is not affiliated with Instructure,
Echo360, the University of Sydney, or any AI provider.

## Quick Start

### 1. Download the correct installer

Open the [latest GitHub release](https://github.com/hanlin317/canvas-lecture-ai/releases/latest)
and choose the package matching your computer:

| Computer | Download |
| --- | --- |
| Apple Silicon Mac (M1, M2, M3, M4, or later) | `arm64.dmg` |
| Intel Mac | `x64.dmg` |
| Most Windows computers | `windows-x64-setup.exe` |
| Windows 11 ARM device | `windows-arm64-setup.exe` |

On macOS, open the DMG and drag **Canvas Lecture AI** into **Applications**. If
Gatekeeper blocks the current non-notarized build, right-click the app in Finder,
choose **Open**, and confirm.

On Windows, run the installer. An unsigned build may show Microsoft Defender
SmartScreen; choose **More info** only after confirming that the installer came
from this repository.

Windows ARM64 can download official Echo360 transcripts, but the installer does
not currently bundle a local Whisper fallback. Use a lecture with an official
transcript, or run the x64 build under Windows emulation when local transcription
is required.

### 2. Complete the seven-step setup

The first launch opens a guided setup:

1. **Welcome:** review what the app downloads and generates.
2. **Storage:** choose your university, download folder, optional NotebookLM
   folder, and optional Obsidian vault.
3. **AI notes:** enable a provider or choose **Do not use AI notes**.
4. **Transcript:** enable local transcription when supported.
5. **Canvas:** log in using your normal institution page and complete MFA.
6. **Courses:** synchronize your Canvas course list.
7. **Ready:** review the settings and open the dashboard.

University of Sydney appears first in the institution list. Choose **Other** to
enter a different complete Canvas URL.

### 3. Download your first lecture

1. Select a course and click **View Course**.
2. Click **Discover Echo360 Lectures**.
3. Open an Echo360 discipline or folder and select lectures. Use **Select new
   lectures** after later discoveries.
4. Choose a quick setup:
   - **Download lectures:** video and slides only.
   - **Transcript pack:** slides, transcript, and local fallback.
   - **Study notes:** transcript plus cloud AI notes.
   - **NotebookLM:** transcript and prepared NotebookLM source.
5. Click **Review and run**. Check the lecture count, outputs, estimated download,
   privacy mode, and AI provider before starting.
6. Follow the progress bar. Jobs can be paused, resumed, reordered, skipped, or
   stopped safely.
7. Find completed files in **Output Library**. Use **Open**, **Show in folder**,
   **Review transcript**, or **Edit note**.

## Common Workflows

### Downloads only

Choose **Download lectures**. This saves available video and slide files without
creating or transmitting a transcript.

### Transcript without AI

Choose **Transcript pack**. The app prefers Echo360's official transcript. If none
exists and local transcription is available, it can transcribe the downloaded
recording on your computer.

### AI study notes

Choose **Study notes**, then select the note format, length, provider, model, and
template under **Advanced output and AI options**. The pre-run summary clearly
states when transcript text will leave your computer.

Available templates include comprehensive notes, exam revision, clinical
procedures, flashcards, learning objectives, concise summary, and practice
questions. The practice-question template keeps answers in a separate answer key.

After generation, **Edit note** opens the Markdown workspace. PDF and Word exports
use native headings, lists, tables, headers, and page numbers. Flashcard notes can
be downloaded as Anki-compatible CSV when they contain `Q:` and `A:` pairs.

### NotebookLM

Choose **NotebookLM**. Canvas Lecture AI creates grounded Markdown source files in
the configured staging folder. Click **Open NotebookLM** and **Show source files**,
then upload those files to a notebook.

Google does not currently publish a consumer NotebookLM source-upload API, so the
final upload remains an assisted handoff. NotebookLM Enterprise uses a separate
Google Cloud API and is not configured by this release.

### Obsidian

Choose a folder containing `.obsidian` during setup or in Settings, enable AI
notes, and select **Save to Obsidian**. Obsidian note filenames use only the
lecture title, while downloaded source files retain course and discipline context.

### Combined study guide

Select lectures that already have editable AI notes and click **Download combined
study guide**. The resulting Markdown file places each lecture in its own section.

## Finding Your Files

Downloads are organized as:

```text
<download folder>/<COURSE> Recorded Lectures/<EXACT ECHO360 FOLDER>/
```

Lecture files use the course, exact Echo360 folder, number within that folder,
and title:

```text
SDDM6321 Periodontics 1 Periodontal Maintenance.mp4
SDDM6321 Periodontics 1 Periodontal Maintenance.pptx
SDDM6321 Periodontics 1 Periodontal Maintenance.txt
```

Obsidian notes use the lecture title:

```text
Periodontal Maintenance.md
```

The **Output Library** searches across courses, disciplines, lectures, and output
types. It is the easiest way to reopen a file when you do not remember its folder.

## Safer Defaults and Privacy

The default workflow selects slides and transcripts, leaves large video downloads
off, and uses local-only processing. Cloud AI must be selected explicitly.

| Operation | Where data goes |
| --- | --- |
| Canvas login and Echo360 discovery | Your institution's Canvas and Echo360 services |
| Video and slide download | Directly to the folder chosen on your computer |
| Official transcript cleaning | Remains on your computer |
| Local Whisper transcription | Remains on your computer |
| Local/Ollama notes | Sent only to `127.0.0.1:11434` by default |
| Cloud AI notes | Transcript text is sent to the selected provider |
| NotebookLM source preparation | Remains local until you upload the file |

API keys entered in Settings are stored in macOS Keychain or Windows Credential
Manager. They are not written to the project configuration, database, page, or
logs. Environment variables are also supported:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export OPENROUTER_API_KEY="..."
export XAI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export MISTRAL_API_KEY="..."
```

Review your institution's policy and the provider's privacy terms before sending
lecture transcripts to a third party. DeepSeek may remain unreachable on networks
that block its API domain even when a valid key is supplied.

See [Privacy](2026-10-25-PRIVACY.md) and
[Security](2026-10-25-SECURITY.md) for more detail.

## Transcripts

Canvas Lecture AI prefers official Echo360 text or VTT transcripts. When no
official transcript is available:

- Apple Silicon uses MLX Whisper.
- Intel Mac and Windows x64 use Faster Whisper.
- Windows x64 can use NVIDIA CUDA on compatible systems and otherwise uses CPU.
- Windows ARM64 does not currently bundle local Whisper.

The first local transcription downloads and caches the selected model. Use
**Review transcript** to correct terminology before creating notes. Saving an edit
creates an `.edited.txt` copy and preserves the original transcript.

## Existing Files and Interrupted Downloads

The default policy skips existing files. Advanced options can instead:

- update only changed files;
- keep both versions with a numbered filename; or
- replace the existing output.

Interrupted downloads retain a `.part` file and resume when the server supports
HTTP Range requests. Quitting the app pauses persistent queue work at its latest
safe checkpoint.

## Notifications and Job Recovery

Enable **Notify me when finished** to receive a browser notification after a job
completes or needs attention. The active job shows its lecture position, stage,
percentage, and ETA. The dashboard's **Continue where you left off** panel returns
to the most recent course or resumes an interrupted batch.

Failed outputs provide a contextual next action such as logging in again,
rediscovering lectures, checking AI settings, reviewing transcript options, or
running readiness checks. **Export sanitized diagnostics** creates a bundle that
excludes API keys, Canvas passwords, transcripts, notes, and downloaded material.

## Troubleshooting

### A course is missing

1. Confirm that Canvas login is saved.
2. Click **Sync Canvas Courses** again.
3. Search every course group, including older or inactive courses.
4. Run **System readiness** if the course is still absent.

### Discovery reports zero recordings

Open the course in Canvas and confirm that its **Recorded Lectures** or Echo360
navigation item works. Then log in again and rerun discovery. Echo360 deployments
vary, and a changed institutional launch link may require an adapter update.

### Echo360 course launch link was not found

The selected Canvas course does not currently expose an Echo360 launch link that
the app can use. Confirm that the course's Recorded Lectures page opens, then use
**Rediscover lectures**. Export sanitized diagnostics if the problem continues.

### Slides are unavailable

The lecturer must make the original presentation available in Echo360. Canvas
Lecture AI cannot download a presentation that Echo360 does not expose.

### Transcript or AI notes are unavailable

AI notes require a transcript. Enable local fallback on a supported computer, or
review whether Echo360 supplied an official transcript. For cloud AI, confirm that
the provider key and selected model are valid.

### Canvas login does not remember credentials

Login uses a dedicated visible Chrome profile so institutional MFA and password
managers can work normally. On macOS, install Apple's iCloud Passwords extension
in that profile to use Passwords app autofill. The app saves browser session state,
not the Canvas password itself.

### Gatekeeper or SmartScreen blocks installation

Current public packages may be unsigned or non-notarized unless the maintainer has
configured Apple Developer ID and Windows Authenticode credentials. Verify the
release URL and SHA-256 checksum before using the documented operating-system
override.

## Updates

Packaged releases check GitHub for updates automatically by default. Automatic
download and installation are opt-in and wait until no lecture job is running.
The app verifies an Ed25519-signed platform manifest and the selected installer's
SHA-256 checksum before installation.

Every version remains available on GitHub Releases. See the
[changelog](2026-10-26-CHANGELOG.md) for version history. Source installations
are updated with Git and do not use the in-app installer.

## Local Application Data

Private state is stored under:

```text
~/Library/Application Support/Canvas Lecture AI/
%LOCALAPPDATA%\Canvas Lecture AI\
```

This includes `config.toml`, browser session state, the dedicated login profile,
SQLite data, logs, diagnostics, model caches, and AI note previews. Existing
installations upgraded from Dental Lecture Manager continue using the legacy
support folder when present so settings and sessions are preserved.

Never attach `local_data`, `config.toml`, browser profiles, or unredacted logs to
a public issue.

## Install From Source

Requirements:

- Python 3.11 or 3.12;
- macOS 14 or later, Windows 10/11 x64, or Windows 11 ARM;
- Google Chrome recommended for password-manager integration;
- an authorized Canvas account.

```bash
git clone https://github.com/hanlin317/canvas-lecture-ai.git
cd canvas-lecture-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[ai,dev]'
python -m playwright install chromium
cp config.example.toml config.toml
python -m app serve
```

On Windows PowerShell, activate with `\.venv\Scripts\Activate.ps1`. Open
`http://127.0.0.1:8017` if the browser does not open automatically.

After installation, these shorter commands are available:

```bash
lecture-download serve
canvas-lecture-ai serve
```

Useful CLI commands:

```bash
python -m app login
python -m app courses
python -m app discover COURSE_CODE
python -m app pipeline --course COURSE_CODE --topic "Echo360 Folder"
python -m app diagnose-canvas --output local_data/diagnostics/canvas-links.json
```

Most configuration is editable in the website. `config.example.toml` documents
the complete source-install format. The server binds to `127.0.0.1`; do not expose
it publicly because the local interface is designed for one trusted user and has
no web authentication.

## Development and Releases

Run the quality gate:

```bash
ruff check app tests scripts/2026-10-26-release-notes.py
pytest -q
```

Build for the current Mac architecture:

```bash
bash 2026-10-25-build-macos.sh
```

Build for the current Windows architecture:

```powershell
.\2026-10-26-build-windows.ps1
```

Pushing a new `v*` tag runs tests and builds Apple Silicon, Intel Mac, Windows
x64, and Windows ARM64 packages. The release workflow attaches installers,
checksums, release notes, and the signed update manifest without replacing older
published versions.

Before tagging a release:

1. Move `Unreleased` changelog entries into a dated semantic-version section.
2. Update versions in `pyproject.toml`, `app/__init__.py`, and the macOS spec.
3. Run the complete quality gate.
4. Commit and push an annotated tag such as `v0.7.0`.

## Known Limitations

- Echo360 private APIs differ between institutions and can change.
- Slides are available only when the lecturer exposes the original presentation.
- Consumer NotebookLM uploads require a manual handoff.
- Provider model names and capabilities can change independently of this app.
- Windows ARM64 uses Windows emulation for bundled x64 Chromium and FFmpeg helpers.
- Windows ARM64 does not bundle local Whisper.
- macOS notarization and Windows Authenticode signing require maintainer certificates.

## License

Canvas Lecture AI is released under the [MIT License](2026-10-25-LICENSE.md).

# Canvas Lecture AI

**Lecture Downloader & AI Notes Generator**

Canvas Lecture AI is a local macOS and Windows application for organizing an authenticated
Canvas and Echo360 lecture workflow. It discovers recordings by their Echo360
folder, downloads available media and slides, cleans transcripts, and prepares
study material for NotebookLM or Obsidian.

The application runs locally on your computer and opens its interface at
`http://127.0.0.1:8017`.

> [!IMPORTANT]
> Use this application only with accounts and educational material you are
> authorized to access. Follow your institution's policies and the copyright
> terms applying to lecture content. Do not redistribute downloaded material
> without permission.

Canvas Lecture AI is an independent, unofficial project. It is not affiliated
with or endorsed by Instructure, Echo360, the University of Sydney, or any AI
provider.

## Features

- Visible Canvas login with MFA and reusable local browser state
- Canvas course sync with active and saved courses grouped for easier browsing
- Echo360 folder and lecture discovery
- Per-folder lecture selection and Select All controls
- Video, original presentation, and official transcript downloads
- Built-in local transcription fallback using MLX Whisper on macOS or Faster Whisper on Windows
- Clean canonical transcripts and deterministic NotebookLM Markdown sources
- AI study notes through OpenAI, Anthropic, Gemini, OpenRouter, xAI, DeepSeek,
  Mistral, or a local OpenAI-compatible service
- Configurable AI note length and per-run note instructions
- Styled Markdown, PDF, and Word exports with headings, lists, and tables
- Obsidian notes and attachments using lecture-title filenames
- Background jobs with progress, ETA, lecture position, cancellation, and
  collapsible processing history
- Local SQLite state and topic manifests

## Download

Download the latest installer for your computer from
[GitHub Releases](https://github.com/hanlin317/canvas-lecture-ai/releases).

### macOS

1. Open the DMG.
2. Drag **Canvas Lecture AI** into **Applications**.
3. Open the app. Its local website opens in your default browser.

The current release is ad-hoc signed but not Apple-notarized. On first launch,
macOS may block it. Right-click the app in Finder, choose **Open**, then confirm
**Open**. A notarized release requires an Apple Developer ID certificate.

The packaged app currently supports Apple Silicon Macs (`arm64`). Intel Macs
can run the project from source but are not covered by the downloadable DMG.

### Windows

1. Download the `windows-x64-setup.exe` file.
2. Run the installer and optionally create a desktop shortcut.
3. Open **Canvas Lecture AI** from the Start menu.

The Windows package supports 64-bit Windows 10 and Windows 11. An unsigned build
may show a Microsoft Defender SmartScreen warning until the project has a
Windows code-signing certificate. Choose **More info**, verify that the download
came from this repository, and select **Run anyway**.

## First Run

Open **Settings** in the website and configure:

- **Canvas URL:** your institution's complete Canvas address
- **Download folder:** where lecture folders will be created
- **NotebookLM folder:** where NotebookLM source files will be staged
- **Obsidian vault:** optional; choose a folder containing `.obsidian`
- **Default AI provider and model:** optional
- **Default note length and instructions:** optional
- **Updates:** choose whether to check, download, or install automatically

Click **Save Configuration**, then **Login to Canvas**. Complete institutional
login and MFA in the visible browser window. The application detects a
successful Canvas page, saves reusable session state locally, and closes the
login window.

Visible login prefers Google Chrome. On macOS, Apple's iCloud Passwords
extension can provide Passwords app autofill in the dedicated Chrome profile.
On Windows, Chrome can save or sync credentials in that profile. The application
does not read or store your Canvas password.

## Typical Workflow

1. Log in to Canvas.
2. Click **Sync Canvas Courses**.
3. Select a course and click **View Course**.
4. Click **Discover Echo360 Lectures**.
5. Select lectures individually or use **Select all** for a discipline.
6. Choose video, slides, transcript, NotebookLM, AI notes, or Obsidian output.
7. Click **Run Pipeline** and follow the progress display.
8. For NotebookLM, click **Open NotebookLM** and **Show source files**, then
   upload the prepared Markdown sources to a new or existing notebook.

Downloads use this structure:

```text
<download folder>/<COURSE> Recorded Lectures/<ECHO360 FOLDER>/
```

Media and transcript filenames contain the course, exact Echo360 folder,
lecture number within that folder, and title. For example:

```text
SDDM6321 Periodontics 1 Periodontal Maintenance.mp4
```

Obsidian notes use only the lecture title:

```text
Periodontal Maintenance.md
```

Existing downloads and notes are not overwritten unless **Overwrite** is
selected.

## NotebookLM

The consumer NotebookLM product accepts the Markdown source files generated by
this app, but Google does not currently publish a consumer source-upload API.
The app therefore provides an assisted handoff: it opens NotebookLM and reveals
the exact prepared source files for upload. NotebookLM Enterprise has a separate
Google Cloud API and is not configured by this consumer-focused release.

## AI Notes

API keys entered in the website are stored in macOS Keychain or Windows
Credential Manager and are never
written back to the page, project configuration, database, or logs. Environment
variables are also supported:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export OPENROUTER_API_KEY="..."
export XAI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export MISTRAL_API_KEY="..."
```

Choose **Refresh models** to query the selected provider's model endpoint. A
successful result is cached locally; the previous cache or built-in list remains
available if refreshing fails. Normal page loads do not contact AI providers.

The note-length presets control both the requested level of detail and the
provider output-token budget. Optional instructions can change emphasis and
organization, but the built-in prompt still requires transcript grounding and
marks unclear source material. Per-run values can override the saved defaults.

PDF and Word exports are rendered as native documents, including real tables,
nested lists, styled headings, page headers, and page numbers. They are not
Markdown text placed inside a document container.

AI transcript content is sent only when an AI operation is selected. Review the
provider's privacy terms and your institution's policy before sending lecture
material to a third-party service. Local/Ollama requests use
`http://127.0.0.1:11434/v1` by default.

## Local Data

The packaged app keeps private state under one of these locations:

```text
~/Library/Application Support/Canvas Lecture AI/
%LOCALAPPDATA%\Canvas Lecture AI\
```

Upgrades from Dental Lecture Manager continue using the existing
`~/Library/Application Support/Dental Lecture Manager/` folder when present, so
saved Canvas sessions, configuration, databases, and AI keys remain available.
New installations use the Canvas Lecture AI folder. Existing macOS Keychain
entries are also read automatically and do not need to be entered again.

This includes `config.toml`, Canvas session state, the dedicated browser profile,
SQLite data, logs, diagnostics, and AI note previews. Downloaded lectures and
exported notes go to the folders selected in Settings.

See [Privacy](2026-10-25-PRIVACY.md) and
[Security](2026-10-25-SECURITY.md) for details. Never attach `local_data`,
`config.toml`, browser profiles, or unredacted diagnostics to a public issue.

## Install From Source

Requirements for a source installation:

- macOS 14 or later on Apple Silicon, or 64-bit Windows 10/11
- Python 3.11 or 3.12
- Google Chrome recommended for password-manager integration
- An authorized Canvas account

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

On Windows PowerShell, activate the environment with
`.\.venv\Scripts\Activate.ps1`; the remaining Python commands are the same.

Open `http://127.0.0.1:8017` if the browser does not open automatically.

After editable installation, the shorter CLI command is also available:

```bash
lecture-download serve
# or
canvas-lecture-ai serve
```

Other commands:

```bash
python -m app login
python -m app courses
python -m app discover COURSE_CODE
python -m app pipeline --course COURSE_CODE --topic "Echo360 Folder"
python -m app diagnose-canvas --output local_data/diagnostics/canvas-links.json
```

## Configuration

Most settings are editable from the website. `config.example.toml` documents
the complete format for source installations. Secrets should stay in the
operating system credential store or environment variables, not `config.toml`.

The application binds to `127.0.0.1` by default. Do not expose it to a public
network: the local web interface is designed for one trusted user and does not
provide web authentication.

## Updates

Packaged releases can check GitHub Releases for updates. Each release contains
an Ed25519-signed, platform-aware manifest; the app verifies that signature and
the selected installer SHA-256 checksum before installation. macOS installation
also validates the app bundle's identity and version and keeps the previous app
as a rollback copy.

Automatic checking is enabled by default. Automatic download and installation
are opt-in, and installation waits until no lecture job is running. Update
checks can be disabled in Settings. Source installations are updated through
Git and cannot use the in-app installer.

Every published version remains available on GitHub Releases with its own Mac
and Windows installers, checksums, signed update manifest, signature, and release notes. See the
[changelog](2026-10-26-CHANGELOG.md) for the complete version history.

## Local Transcript Fallback

When Echo360 has no official transcript, the app can transcribe the downloaded
lecture locally. Apple Silicon Macs use MLX Whisper; Windows uses Faster Whisper
with NVIDIA CUDA acceleration when available and an efficient CPU mode otherwise.
The default model is downloaded on first use and cached under the app's private
data folder. Audio is not sent to an AI provider. The pipeline does not
retranscribe a lecture when a canonical transcript already exists unless
overwrite is enabled.

## Development

Run the quality gate:

```bash
ruff check app tests scripts/2026-10-26-release-notes.py
pytest -q
```

Build an Apple Silicon app and DMG:

```bash
bash 2026-10-25-build-macos.sh
```

Build a Windows x64 installer from PowerShell on Windows:

```powershell
.\2026-10-26-build-windows.ps1
```

The build creates an isolated packaging environment, downloads Playwright
Chromium, bundles an FFmpeg binary, creates `dist/Canvas Lecture AI.app`, ad-hoc
signs it, and writes the versioned DMG plus SHA-256 checksum to `dist/`.

Pushing a `v*` tag runs the tests and both platform builds on GitHub Actions,
then attaches the DMG, Windows installer, checksums, signed update manifest, and
signature to a GitHub release.

### Publishing a release

1. Move the relevant items from `Unreleased` in
   [`2026-10-26-CHANGELOG.md`](2026-10-26-CHANGELOG.md) into a new dated semantic-version section.
2. Update the matching version in `pyproject.toml`, `app/__init__.py`, and the
   macOS PyInstaller specification. The Windows installer reads this version at build time.
3. Run `ruff check app tests scripts/2026-10-26-release-notes.py` and `pytest -q`, then commit the release.
4. Create and push a new annotated tag, such as `v0.4.0`.

The release workflow validates all version fields and extracts the matching
changelog entry as the GitHub release description. Existing releases are
immutable: rerunning a published tag fails instead of replacing its assets.

## Known Limitations

- Echo360 deployments differ between institutions and may require adapter
  updates when their private APIs change.
- Slides can be downloaded only when the lecturer has made the original
  presentation available.
- Background work is executed by the local app process; quitting the app stops
  a running job.
- Provider model endpoints and model capabilities can change independently of
  this application.
- The DMG is Apple Silicon-only and is not notarized.
- Windows releases are x64-only and will show SmartScreen warnings until they
  are signed with a trusted Windows code-signing certificate.

## License

Canvas Lecture AI is released under the
[MIT License](2026-10-25-LICENSE.md).

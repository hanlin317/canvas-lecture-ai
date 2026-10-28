# Changelog

All notable changes to Canvas Lecture AI are documented here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and published release assets are
immutable.

## [Unreleased]

## [0.6.0] - 2026-10-29

### Added

- Persistent multi-lecture queues with per-lecture progress, pause, resume, skip, retry, and queue
  reordering. Interrupted pipeline jobs return as paused work after an application restart.
- Resumable media downloads, checksum tracking, and explicit skip, replace, keep-both, and
  update-changed policies for existing files.
- An editable AI note workspace with Markdown saving, native PDF and Word export, exact-section
  regeneration, six study-note templates, and global or per-course pipeline profiles.
- Downloads-only, local-processing, and cloud-AI privacy modes with clear controls for when lecture
  content may leave the computer.
- A system-readiness panel and sanitized diagnostics export covering storage, FFmpeg, local
  transcription, Canvas authentication, AI configuration, and Echo360 access.
- Downloadable Intel Mac and Windows ARM64 packages alongside Apple Silicon and Windows x64 builds.
- Optional Apple Developer ID signing and notarization plus optional Windows Authenticode signing in
  the release workflow.

### Changed

- Improved transcript cleanup for repeated captions and common leading speech fillers.
- Expanded job status displays with the current lecture, safe checkpoint, overall progress, and ETA.
- Updated packaging, update manifests, release notes, privacy guidance, and security documentation
  for four platform architectures.

### Security

- Diagnostics omit credentials, session files, signed media URLs, lecture titles, transcripts,
  generated notes, local paths, and API keys.
- Release signing credentials are read only from GitHub Actions secrets and are never bundled with
  the application.

## [0.5.0] - 2026-10-26

### Added

- A Windows 10/11 x64 application build with a per-user Inno Setup installer, Start menu entry,
  optional desktop shortcut, bundled Chromium, FFmpeg, and document exporters.
- Faster Whisper local transcription on Windows, using NVIDIA CUDA when available and an efficient
  CPU fallback otherwise.
- Native Windows folder selection, Explorer integration, and Windows Credential Manager storage
  for AI provider keys.

### Changed

- Made application data paths, onboarding text, local transcription settings, and packaged runtime
  checks platform-aware while preserving existing macOS behavior.
- Extended automatic updates and signed update manifests to select and verify the correct macOS or
  Windows installer.
- Expanded the release workflow to test on Linux and build and verify both native desktop packages
  before publishing an immutable release.

### Security

- Windows API keys are stored in Windows Credential Manager rather than configuration files.
- Windows installer downloads are verified against the SHA-256 digest in the signed update
  manifest before installation.

## [0.4.0] - 2026-10-26

### Added

- A seven-step first-run setup guide covering storage, AI notes, transcription, Canvas login,
  course sync, and a final configuration review.
- A searchable university Canvas picker with University of Sydney first, verified presets for
  common Australian universities, and custom URL entry for other institutions.
- An explicit option to use the downloader without configuring or generating AI notes.
- Persistent onboarding progress and a Settings action to run the guide again.

### Changed

- AI provider preferences are retained when AI notes are disabled, making it possible to turn the
  feature back on without re-entering the configuration.
- The dashboard disables AI-only controls when AI notes are not configured while leaving lecture,
  slide, transcript, NotebookLM, and Obsidian workflows available.

## [0.3.0] - 2026-10-26

### Added

- Rebranded the application as Canvas Lecture AI with the subtitle Lecture Downloader & AI Notes
  Generator.
- Native PDF and Word exports with headings, lists, tables, links, code blocks, headers, and page
  numbers.
- Configurable AI providers, live provider model refresh, note lengths, custom instructions, and
  Markdown, PDF, or Word output.
- Slide downloads, topic-local lecture numbering, per-topic selection, cancellable background jobs,
  progress percentages, lecture position, and estimated time remaining.
- Signed automatic updates with DMG checksum, bundle identity, version validation, and rollback.
- A packaged local MLX Whisper fallback and improved Echo360 course, folder, and media discovery.

### Changed

- Improved transcript cleaning and the clinical study-note prompt while keeping output grounded in
  lecture evidence.
- Simplified Obsidian filenames to use the lecture title while retaining structured metadata.
- Moved configuration into the web interface and preserved legacy application data and Keychain
  access after the rebrand.

### Security

- Stored provider API keys in macOS Keychain.
- Added Ed25519 signatures to update manifests and verified downloaded DMGs before installation.

## [0.2.0] - 2026-10-25

### Added

- Initial local macOS application for Canvas login, course sync, and Echo360 lecture discovery.
- Video, slide, official transcript, and local MLX Whisper download workflows.
- Deterministic lecture folders, cleaned transcripts, NotebookLM source preparation, AI study notes,
  and Obsidian export.
- Local SQLite state, processing history, course grouping, and a browser-based management interface.
- Apple Silicon DMG packaging and the first GitHub release workflow.

[Unreleased]: https://github.com/hanlin317/canvas-lecture-ai/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/hanlin317/canvas-lecture-ai/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/hanlin317/canvas-lecture-ai/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/hanlin317/canvas-lecture-ai/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/hanlin317/canvas-lecture-ai/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/hanlin317/canvas-lecture-ai/releases/tag/v0.2.0

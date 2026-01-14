# DCC Backlog

## Persistence Layer (`~/.dcc/`)

### Directory Structure

```
~/.dcc/
├── findings.json      # Latest analyzer scan results (read by TUI)
├── snoozed.json       # User-snoozed items with expiry dates
└── logs/              # Analyzer run logs (optional)
    └── dcc-scout-2026-01-14.log
```

### findings.json

Produced by `dcc-scout`, consumed by `dcc` TUI.

```json
{
  "generated": "2026-01-14T12:00:00",
  "scan_duration_sec": 45,
  "total_reclaimable_bytes": 50812345678,
  "item_count": 15,
  "findings": [
    {
      "target": "~/ws/old-project/node_modules",
      "category": "node",
      "size_bytes": 4521897234,
      "size_human": "4.2 GB",
      "file_count": 48293,
      "last_modified": "2025-08-15T09:12:00",
      "last_accessed": "2025-08-15T09:12:00",
      "staleness_days": 152,
      "parent_project": "~/ws/old-project",
      "is_archive": false,
      "options": [
        {"id": "delete", "reclaim_bytes": 4521897234, "reversible": false},
        {"id": "compress", "reclaim_bytes": 3600000000, "reversible": true}
      ],
      "recommendation": "delete",
      "reason": "npm install"
    }
  ]
}
```

**Lifecycle:**
1. `dcc-scout` scans filesystem, writes findings.json
2. `dcc` TUI reads findings.json on startup
3. TUI filters out items present in snoozed.json (unless expired)
4. User reviews, marks, executes actions

### snoozed.json

Managed by TUI when user presses 's' to snooze. Read by both analyzer and TUI.

```json
{
  "version": 1,
  "default_snooze_days": 14,
  "items": [
    {
      "target": "~/ws/old-project/node_modules",
      "snoozed_at": "2026-01-14T10:30:00",
      "expires_at": "2026-01-28T10:30:00"
    },
    {
      "target": "~/Library/Application Support/Slack/Cache",
      "snoozed_at": "2026-01-10T08:00:00",
      "expires_at": "2026-01-24T08:00:00"
    }
  ]
}
```

**Behavior:**
- TUI writes new entry when user snoozes (sets expires_at = now + 14 days)
- TUI removes entry when user un-snoozes
- TUI filters snoozed items from display (shows dimmed with 'z' marker)
- Expired snoozes auto-removed on next TUI load or analyze run
- No permanent ignore - everything resurfaces eventually

**TUI integration:**
- On startup: load snoozed.json, mark matching findings as snoozed
- On 's' key: toggle snooze state, update snoozed.json immediately
- On quit: ensure snoozed.json is up to date

---

## Backlog Items

### P0 - Core Functionality

- [ ] **Persistence: Load/save snoozed.json**
  - TUI reads ~/.dcc/snoozed.json on startup
  - TUI writes snoozed.json when user toggles snooze
  - Create ~/.dcc/ directory if not exists
  - Handle missing/corrupt file gracefully

- [ ] **Persistence: Load findings from ~/.dcc/findings.json**
  - TUI defaults to ~/.dcc/findings.json if no arg provided
  - Fall back to test/sample-findings.json for development
  - Show clear error if no findings available

- [ ] **Action execution: delete**
  - Actually run `rm -rf` for delete actions
  - Require confirmation (already have confirm screen)
  - Print post-execution report

- [ ] **Action execution: compress (zip)**
  - Create `.archived.zip` with `zip -9 -r`
  - Create `.archived-restore` executable script
  - Remove original after successful compression
  - Handle errors gracefully (disk full, permissions)

- [ ] **Action execution: git-gc**
  - Run `git gc --aggressive` in target repo
  - Show before/after size comparison

### P1 - Analyzer

- [ ] **dcc-scout: Basic scanner**
  - Scan home directory for large files (>1GB)
  - Detect stale node_modules, target/, venv/
  - Output findings.json to ~/.dcc/
  - Respect snoozed.json (exclude snoozed items)

- [ ] **dcc-scout: Build artifact detection**
  - Python: venv/, .venv/, __pycache__/, .pytest_cache/
  - Node.js: node_modules/, .npm/, .yarn/
  - Rust: target/
  - Go: pkg/, module cache
  - .NET: bin/, obj/
  - Java: target/, build/, .gradle/
  - iOS/macOS: DerivedData/, Pods/, .build/
  - General: dist/, build/, out/, coverage/

- [ ] **dcc-scout: Staleness calculation**
  - Use project marker (package.json, Cargo.toml, etc.) mtime
  - Default threshold: 30 days
  - Calculate staleness_days for each finding

- [ ] **dcc-scout: Git repository analysis**
  - Detect repos with many loose objects (`git count-objects -v`)
  - Calculate potential gc savings
  - Warn if unpushed changes exist

- [ ] **dcc-scout: Application analysis**
  - Scan /Applications and ~/Applications
  - Get last used date via `mdls -name kMDItemLastUsedDate`
  - Flag apps >1GB not used in 90+ days

- [ ] **dcc-scout: Library leftovers**
  - Scan ~/Library/Application Support/
  - Match against installed apps
  - Flag orphaned support directories

- [ ] **dcc-scout: Cache directories**
  - ~/Library/Caches/
  - Report total size per app
  - Flag stale caches

- [ ] **dcc-scout: Log file detection**
  - Find *.log files >100MB
  - Check if actively written (lsof)
  - Suggest truncate/rotate/delete

### P2 - Scheduling & Notifications

- [ ] **launchd plist for daily runs**
  - ~/Library/LaunchAgents/com.user.dcc-scout.plist
  - Run daily at 12:00 (noon)
  - Low priority (nice)
  - Log to ~/.dcc/logs/

- [ ] **macOS notifications**
  - Notify when significant savings found (>5GB default)
  - Use osascript or terminal-notifier
  - Link to run `dcc` command

### P3 - Configuration

- [ ] **Config file support**
  - Location: ~/.config/dcc/config.yaml or ~/.dcc/config.yaml
  - Thresholds: large_file_min_gb, stale_project_days, stale_app_days
  - Scan paths and exclusions
  - Snooze duration

### P4 - Future Enhancements

- [ ] Deduplication detection (multiple Joplin backups, similar large files)
- [ ] Time Machine snapshot analysis
- [ ] Docker image/container cleanup
- [ ] Homebrew cache cleanup (`brew cleanup --dry-run`)
- [ ] npm/pip/cargo global cache analysis
- [ ] Spotlight index size monitoring
- [ ] Cloud storage integration (OneDrive, iCloud cloud-only detection)
- [ ] HTML report with interactive filtering
- [ ] Archive detection (find .archived.zip from previous runs)
- [ ] Restore action for archives

---

## Open Questions

1. Config location: `~/.config/dcc/` vs `~/.dcc/config.yaml`?
2. Should analyzer skip items already in findings.json that haven't changed?
3. Historical trend data - worth tracking disk usage over time?

---

## Completed

- [x] Interactive TUI with Textual framework
- [x] Finding visualization (size, category, action, staleness)
- [x] Keyboard navigation (↑↓ navigate, ←→ cycle actions)
- [x] Mark/unmark items (space)
- [x] Snooze toggle (s) - UI only, no persistence yet
- [x] Inspect in Finder (i)
- [x] Details panel with action cycling
- [x] Confirmation screen
- [x] Execute binding (ctrl+x)
- [x] Quit binding (ctrl+q)

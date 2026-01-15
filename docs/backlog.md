# DCC Backlog

## Persistence Layer (`~/.dcc/`)

### Directory Structure

```
~/.dcc/
├── scan.json          # Latest scanner results (read by TUI)
├── snoozed.json       # User-snoozed items with expiry dates
└── logs/              # Analyzer run logs (optional)
    └── dcc-scout-2026-01-14.log
```

### scan.json

Produced by `dcc-scout`, consumed by TUI.

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
1. `dcc-scout` scans filesystem, writes scan.json
2. TUI reads scan.json on startup
3. TUI filters out snoozed items (unless expired) and cleaned items
4. User reviews, marks, executes actions

### snoozed.json

Managed by TUI when user presses 'z' to snooze. Saved on ctrl+x.

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
- On startup: load snoozed.json, mark matching findings as snoozed, hide from list
- On 'z' key: toggle snooze state in memory
- On ctrl+x: save newly snoozed items to snoozed.json

---

## Backlog Items

### P0 - Core Functionality

- [x] **Persistence: Load/save snoozed.json** ✓
- [x] **Persistence: Load findings from ~/.dcc/scan.json** ✓
- [x] **Action execution: delete** ✓
- [x] **Action execution: git-gc** ✓
- [x] **Action execution: ollama-rm** ✓
- [x] **Action execution: hf-delete** ✓
- [x] **Cleaned items detection** ✓ (hides already-deleted items on startup)
- [ ] **Action execution: compress (zip)**
  - Create `.archived.zip` with `zip -9 -r`
  - Create `.archived-restore` executable script
  - Remove original after successful compression

### P1 - Analyzer

- [x] **dcc-scout: Basic scanner** ✓
- [x] **dcc-scout: Build artifact detection** ✓
- [x] **dcc-scout: Staleness calculation** ✓
- [x] **dcc-scout: Git repository analysis** ✓
- [x] **dcc-scout: Application analysis** ✓
- [x] **dcc-scout: Library leftovers** ✓
- [x] **dcc-scout: Cache directories** ✓
- [x] **dcc-scout: Ollama models** ✓
- [x] **dcc-scout: Huggingface models** ✓
- [ ] **dcc-scout: Log file detection**
  - Find *.log files >100MB
  - Check if actively written (lsof)
  - Suggest truncate/rotate/delete

### P2 - Scheduling & Notifications

- [x] **Cron job for daily runs** ✓ (via Makefile)
- [ ] **macOS notifications**
  - Notify when significant savings found (>5GB default)
  - Use osascript or terminal-notifier

### P3 - Configuration

- [ ] **Config file support**
  - Location: ~/.dcc/config.yaml
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
- [x] Snooze toggle (z) with persistence to ~/.dcc/snoozed.json
- [x] Hide snoozed items from list on startup
- [x] Inspect in Finder (i)
- [x] Details panel with action cycling
- [x] Confirmation screen with actual shell commands
- [x] Execute binding (ctrl+x) with command execution
- [x] Quit binding (ctrl+q)
- [x] dcc-scout scanner with 9 detection phases
- [x] Ollama model scanning with human-readable names
- [x] Huggingface model scanning
- [x] Cleaned items detection (hide deleted items on restart)
- [x] CLI wrapper (dcc) with auto-scan if stale
- [x] Makefile for installation and cron setup

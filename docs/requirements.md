# DCC - Disk Cleanup Consultant

## Implementation Status

**Implemented:**
- Interactive TUI (`dcc`) with Textual framework
- Finding visualization with size, category, action, staleness
- Keyboard navigation (↑↓ navigate, ←→ cycle actions, space mark)
- Details panel with action cycling
- Inspect function (reveal in Finder)
- Confirmation screen

**Not yet implemented:**
- Analyzer daemon (`dcc-scout`)
- Actual action execution (delete, compress, git-gc)
- Compression with rollback scripts
- Snooze mechanism
- Configuration file
- launchd scheduling
- macOS notifications

## Overview

A two-part system for identifying and executing disk space optimization:

1. **Analyzer** (`dcc-scout`) - Scheduled daemon that scans and produces structured recommendations (not yet implemented)
2. **Interactive CLI** (`dcc`) - Textual TUI to review, select, and execute actions

## Goals

- Identify wasted disk space with minimal false positives
- Provide multiple action options per target (delete, compress, git gc, etc.)
- Rank by potential space savings
- Interactive selection with batch execution and approval gate
- Safe compression with trivial rollback
- Run analyzer unattended daily via launchd

---

## Architecture

### Data Model

Each finding produces a structured record with all decision-relevant data:

```yaml
- target: ~/ws/old-project/node_modules
  category: build_artifact

  # Size info
  size_bytes: 4521897234
  size_human: "4.2 GB"
  file_count: 48293

  # Time info
  created: 2025-06-10T14:23:00
  last_modified: 2025-08-15T09:12:00
  last_accessed: 2025-08-15T09:12:00
  staleness_days: 152

  # Context
  parent_project: ~/ws/old-project
  parent_last_modified: 2025-08-15T09:12:00  # any file in project
  project_marker: package.json

  # For archives from previous cleanup cycles
  is_archive: false
  archived_date: null  # or 2025-12-01 if previously archived
  original_size_bytes: null  # size before archiving

  # Options
  options:
    - id: delete
      action: "rm -rf '~/ws/old-project/node_modules'"
      reclaim_bytes: 4521897234
      reversible: false
    - id: compress
      action: "dcc-compress '~/ws/old-project/node_modules'"
      reclaim_bytes: 3800000000  # estimated ~85% for node_modules
      reversible: true

  recommendation: delete
  reason: "Node dependencies, restore: npm install"
  restore_hint: "npm install"  # how to get it back if deleted
```

### Archive Detection

Previously archived items (`.archived.zip` files) remain candidates for future cleanup:

```yaml
- target: ~/ws/old-project/node_modules.archived.zip
  category: archive
  size_bytes: 892345678
  size_human: "851 MB"

  # Archive metadata (read from .archived-restore script)
  is_archive: true
  archived_date: 2025-12-01
  original_size_bytes: 4521897234
  original_size_human: "4.2 GB"
  days_since_archived: 44

  options:
    - id: delete
      action: "rm '~/ws/old-project/node_modules.archived.zip' '~/ws/old-project/node_modules.archived-restore'"
      reclaim_bytes: 892345678
      reversible: false
    - id: restore
      action: "~/ws/old-project/node_modules.archived-restore"
      reclaim_bytes: 0  # actually uses more space
      reversible: true

  recommendation: delete  # if archived 30+ days, probably safe
  reason: "Archived 44 days ago, never restored"
```

### Action Types

Actions displayed as lowercase verbs:

- `del` - Delete permanently (irreversible)
- `zip` - Compress with rollback script (reversible)
- `gc` - Run `git gc --aggressive` (irreversible)
- `rst` - Restore from archive (reversible)
- `---` - Skip / no action

### Category Labels

Categories displayed as readable labels (not abbreviations):

- `App` - Applications
- `Node` - Node.js (node_modules)
- `Rust` - Rust (target/)
- `Venv` - Python virtual environments
- `Model` - LLM models (Ollama, etc.)
- `Cache` - System/app caches
- `Logs` - Log files
- `Git` - Git repositories
- `Backup` - Backup files
- `Archiv` - Previously archived items
- `Orphan` - Leftover files from uninstalled apps
- `File` - Generic large files
- `Data` - Data directories (ML checkpoints, etc.)

### Compression with Rollback

Directory structure preserved for ccd navigation. Zips stay **inside** the target directory.

**Example: Compressing `~/ws/old-project/node_modules`**

Before:
```
~/ws/old-project/
├── node_modules/        # 4.2 GB
├── package.json
└── src/
```

After:
```
~/ws/old-project/
├── node_modules.archived.zip       # compressed archive
├── node_modules.archived-restore   # executable restore script
├── package.json
└── src/
```

**Example: Compressing entire project contents**

After:
```
~/ws/old-project/                  # directory preserved for ccd
├── .archived.zip                   # all original contents
└── .archived-restore               # restore script
```

**Restore script contents:**
```bash
#!/bin/bash
# Restore node_modules from cleanup archive
# Original size: 4.2 GB
# Compressed: 2026-01-14 12:00:00
set -e
cd "$(dirname "$0")"
unzip -q "node_modules.archived.zip" -d .
rm "node_modules.archived.zip" "node_modules.archived-restore"
echo "Restored: node_modules"
```

**Properties:**
- Parent directory always preserved (ccd keeps working)
- Restore script is self-contained, no dependencies
- Running restore removes zip and script (clean state)
- Archive uses maximum compression (`zip -9`)
- Preserves permissions and timestamps

---

## Interactive CLI (`dcc`)

**Framework:** Python Textual TUI (full terminal control, proper keyboard navigation)

### Layout (Compact, ~80 cols)

```
╭─────────────────────────────────────────────────────────────────────╮
│ DCC Disk Cleanup Consultant │ 47.3 GB reclaimable │ 0 marked (0 B)  │
├─────────────────────────────────────────────────────────────────────┤
│   12.1GB App    del 227d /Applications/Xcode.app                    │
│    5.0GB Data   zip  81d ~/ws/ml-experiments/checkpoints            │
│    4.8GB File   del 147d ~/Downloads/ubuntu-24.04-desktop-amd64.iso │
│ *  4.2GB Node   del 152d ~/ws/old-project/node_modules              │
├─────────────────────────────────────────────────────────────────────┤
│ ~/ws/old-project/node_modules                                       │
│ 4.2 GB · Node · 152d stale · npm install                            │
│ ◀ del ▶  zip                                                        │
╰─────────────────────────────────────────────────────────────────────╯
  ↑↓ navigate  ←→ action  space mark  i inspect  x execute  q quit
```

### List Item Format (Single Line)

```
*  4.2GB Node   del 152d ~/ws/old-project/node_modules
│  │     │      │   │    └─ Target path (truncated if needed)
│  │     │      │   └─ Staleness (days since last access)
│  │     │      └─ Action: del/zip/gc/rst/---
│  │     └─ Category label (App, Node, Rust, Venv, Model, etc.)
│  └─ Size (human-readable)
└─ Mark indicator (* = marked, space = unmarked)
```

### Keyboard Navigation

**Main View (single screen, no drill-down):**

- `↑/↓` - Navigate items (sorted by size, biggest first)
- `←/→` - Cycle through available actions for current item
- `SPACE` - Mark/unmark item for execution
- `i` - Inspect: reveal target in Finder
- `a` - Mark all items
- `n` - Clear all marks
- `x` - Execute marked items → confirmation screen
- `q` - Quit

**Confirmation Screen:**

- `y` - Execute all marked actions
- `n` or `ESC` - Cancel, return to main view

### Colors

- **Cyan** - Current row highlight (cursor position)
- **Orange** - Marked items (background highlight)
- **Magenta spectrum** - Navigation bar, action indicators
- **Rainbow** - Details panel labels (visual interest)
- **Dim** - Secondary info
- ~~Blue~~ - Avoided (poor readability on dark terminals)

### Confirmation Screen

```
╭─────────────────────────────────────────────────────────────╮
│ Confirm Actions                                             │
├─────────────────────────────────────────────────────────────┤
│ 🗑️  DELETE (permanent):                                     │
│     12.1 GB  /Applications/Xcode.app                        │
│      4.2 GB  ~/ws/old-project/node_modules                  │
│     ────────────────────                                    │
│     16.3 GB                                                 │
│                                                             │
│ 📦 COMPRESS (reversible):                                   │
│      5.0 GB  ~/ws/ml-experiments/checkpoints                │
│     ────────────────────                                    │
│     ~4.0 GB savings                                         │
│                                                             │
│ ════════════════════════════════════════════════════════    │
│ ESTIMATED SAVINGS: 20.3 GB                                  │
╰─────────────────────────────────────────────────────────────╯
```

### Post-Execution Report

```
✓ Deleted /Applications/Xcode.app (12.1 GB)
✓ Compressed ~/ws/ml-experiments/checkpoints (5.0 GB → 1.0 GB)
  └─ Restore: ~/ws/ml-experiments/checkpoints/.archived-restore

Reclaimed: 20.3 GB
```

---

## Detection Categories

### 1. Large Files (>1GB)

Scan home directory for files exceeding threshold, excluding:
- Active VM disks (UTM, Colima, OrbStack) - flag but don't recommend deletion
- OneDrive cloud-only placeholders (0B actual size)

**Report includes:**
- File path, size, last accessed/modified time
- Category (VM, model, backup, media, unknown)
- Staleness indicator (not accessed in X days)

---

### 2. Build Artifacts & Dependencies

Detect stale project dependencies using markers from `~/.ccd.prune` and patterns from `~/.ccd.ignore`.

**Target directories:**

| Language | Artifacts |
|----------|-----------|
| Python | `venv/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.egg-info/` |
| Node.js | `node_modules/`, `.npm/`, `.yarn/`, `.pnpm-store/` |
| Rust | `target/` |
| Go | `pkg/`, module cache |
| .NET | `bin/`, `obj/`, `packages/` |
| Java | `target/`, `build/`, `.gradle/` |
| iOS/macOS | `DerivedData/`, `Pods/`, `.build/` |
| General | `dist/`, `build/`, `out/`, `coverage/`, `.cache/`, `.parcel-cache/`, `.next/`, `.nuxt/` |

**Staleness criteria:**
- Parent project not modified in X days (default: 30)
- Use project marker (e.g., `package.json` mtime) as reference

**Report includes:**
- Project path, artifact type, size
- Last project activity date
- Suggested cleanup command

---

### 3. Git Repository Optimization

Identify repos that could benefit from maintenance:

**Checks:**
- Large `.git/objects/pack` files relative to working tree
- Repos with many loose objects (run `git count-objects -v`)
- Repos not fetched/pulled in X days (stale remotes)
- Large files in history (candidates for `git filter-repo`)

**Report includes:**
- Repo path, `.git` size vs working tree size
- Potential savings from `git gc --aggressive`
- Warning if repo has unpushed changes

---

### 4. Compression Opportunities

Identify large files/directories not accessed recently that could be compressed:

**Candidates:**
- Directories >500MB not accessed in 90+ days
- Large media files, logs, exports
- Old project archives

**Exclusions:**
- Already compressed formats (`.zip`, `.gz`, `.7z`, `.dmg`)
- Active databases, VM disks

**Report includes:**
- Path, current size, estimated compressed size
- Last access date
- Suggested compression command

---

### 5. Applications Analysis

Scan `/Applications` and `~/Applications`:

**Checks:**
- App size (especially >1GB)
- Last opened date (via `mdls -name kMDItemLastUsedDate`)
- Apps not opened in X days (default: 90)

**Report includes:**
- App name, size, last used date
- Whether app is from App Store (easier to reinstall)
- Related support files size (see Library leftovers)

---

### 6. Library Leftovers

Scan `~/Library` for orphaned application data:

**Target locations:**
- `~/Library/Application Support/` - match against installed apps
- `~/Library/Caches/` - safe to clear, report total size
- `~/Library/Containers/` - match against installed apps
- `~/Library/Group Containers/` - match against installed apps
- `~/Library/Preferences/` - orphaned plists
- `~/Library/Logs/` - old log files
- `~/Library/Saved Application State/` - orphaned states

**Detection logic:**
- Extract app identifier from support folder name
- Check if corresponding app exists in /Applications
- Flag folders for uninstalled apps

**Report includes:**
- Orphan path, size, associated app name
- Confidence level (high/medium/low)
- Safe cleanup command

---

### 7. Oversized Log Files

Detect log files and log-like text files that have grown excessively large (>100MB).

**Detection methods:**
- Filename patterns: `*.log`, `*.log.*`, `*-log`, `*_log`, `*.out`, `*.err`
- Common log locations: `~/Library/Logs/`, `~/.local/share/*/logs/`, project `logs/` dirs
- Content heuristics for unmarked files:
  - Repeated line structure (timestamps, levels)
  - High line count relative to size (text-heavy)
  - Common log patterns: `[INFO]`, `[ERROR]`, `DEBUG`, timestamps

**Analysis:**
- Check if file is actively written (lsof or mtime within last hour)
- Identify rotatable logs vs single growing files
- Detect logs that should have rotation but don't

**Report includes:**
- File path, size, line count estimate
- Whether actively in use
- Growth rate if historical data available
- Suggested action: truncate, rotate, or delete

**Thresholds (configurable):**
```yaml
log_file_max_mb: 100
log_file_patterns:
  - "*.log"
  - "*.log.*"
  - "*.out"
  - "*.err"
```

---

### 8. Duplicate Detection (Optional/Future)

Identify potential duplicates:
- Multiple Joplin backups (keep only N most recent)
- Similar large files by hash
- Redundant downloads

---

## Configuration (not yet implemented)

Location: `~/.config/dcc/config.yaml`

```yaml
# Scanning
scan_paths:
  - ~/
exclude_paths:
  - ~/Library/Mobile Documents  # iCloud
  - ~/.Trash

# Thresholds
large_file_min_gb: 1
stale_project_days: 30
stale_app_days: 90
compression_candidate_days: 90
compression_min_mb: 500

# Log files
log_file_max_mb: 100
log_file_patterns:
  - "*.log"
  - "*.log.*"
  - "*.out"
  - "*.err"

# Git
git_gc_threshold_mb: 100
git_stale_remote_days: 60

# Output
output_dir: ~/.local/share/dcc
output_format: markdown  # or json, html
keep_reports: 30  # days

# Notifications
notify_terminal: true
notify_system: true  # macOS notification when significant savings found
notify_threshold_gb: 5  # minimum savings to trigger notification

# Snooze
snooze_days: 14  # re-surface dismissed items after this period
snooze_file: ~/.local/share/dcc/snoozed.yaml

# Compression
compress_level: 9  # max compression
compress_suffix: ".archived.zip"
restore_suffix: ".archived-restore"

# Interactive CLI
default_action: recommend  # pre-select recommended actions
show_reversible_first: false  # or prioritize by size
```

---

## Output Format

### Analyzer Output (not yet implemented)

Primary output: `~/.local/share/dcc/findings.json`

```yaml
generated: 2026-01-14T12:00:00
scan_duration_sec: 45
total_reclaimable_bytes: 50812345678
item_count: 23

findings:
  - target: ~/ws/old-project/node_modules
    category: build_artifact
    size_bytes: 4521897234
    last_accessed: 2025-08-15
    staleness_days: 152
    options:
      - id: delete
        action: "rm -rf '~/ws/old-project/node_modules'"
        reclaim_bytes: 4521897234
        reversible: false
      - id: compress
        action: "dcc-compress '~/ws/old-project/node_modules'"
        reclaim_bytes: 3800000000
        reversible: true
    recommendation: delete
    reason: "Node dependencies, restore with: npm install"
  # ... more findings
```

### Human-Readable Report (optional, not yet implemented)

Also generates `~/.local/share/dcc/report.md` for quick review without interactive CLI.

---

## Scheduling & Components

### Components

- `dcc-scout` - Scan and produce findings.json (scheduled via launchd) - **not yet implemented**
- `dcc` - Interactive TUI to review and execute actions (manual)
- `dcc-compress` - Compress with rollback script (called by dcc) - **not yet implemented**
- `dcc snooze <path>` - Snooze a recommendation (manual) - **not yet implemented**

### launchd plist (not yet implemented)

Location: `~/Library/LaunchAgents/com.user.dcc-scout.plist`

- Run daily at 12:00 (noon)
- Run on wake if missed (StartCalendarInterval + launchd catch-up)
- Low priority (nice)
- Log output to `~/.local/share/dcc/logs/`
- Send macOS notification on completion if savings > threshold

---

## Implementation Notes

### Language Choice
- Python (available on macOS, good filesystem APIs)
- Interactive CLI uses Textual (Python TUI framework)
- Analyzer has no external dependencies beyond stdlib

### Performance
- Use `os.scandir()` for efficient directory traversal
- Parallel scanning with `concurrent.futures`
- Cache results between categories to avoid re-scanning
- Skip scanning if last scan <12h ago (configurable)

### Safety
- NEVER delete anything automatically
- Commands in report are copy-paste ready but not executed
- Warn before suggesting deletion of anything with recent access
- Flag items with unpushed git changes

### Snooze Mechanism (not yet implemented)
- User can snooze individual recommendations via: `dcc snooze <path>`
- Snoozed items stored with timestamp in `~/.local/share/dcc/snoozed.yaml`
- Items re-appear after `snooze_days` (default: 14)
- No permanent ignore - everything resurfaces eventually
- Snooze file format:
  ```yaml
  snoozed:
    - path: ~/ws/old-project/node_modules
      until: 2026-01-28
      reason: "keeping for reference"  # optional
  ```

### Compression Rollback Design
- Archive and restore script placed **inside parent directory** (preserves ccd navigation)
- Restore script is executable, self-documenting, self-deleting
- No central registry needed - rollback is local and obvious
- Naming: `<target>.archived.zip` + `<target>.archived-restore` (inside parent)
- For full directory compression: `.archived.zip` + `.archived-restore` (hidden, inside target)
- Restore script includes:
  - Creation timestamp
  - Original path
  - Original size
  - Instructions in comments
- Running restore script:
  1. Extracts archive in place
  2. Removes zip file
  3. Removes itself
  4. Prints confirmation

---

## Decisions

1. **Independent scanning** - ccd excludes `~/Library` and other paths we need. Reuse ccd's pattern definitions (`.ccd.ignore`, `.ccd.prune`) but scan independently.
2. **macOS notifications** - Yes, notify when significant savings found.
3. **Snooze logic** - Track dismissed recommendations. Re-surface after configurable period (default: 14 days). Never permanently ignore.
4. **Schedule** - Run daily at 12:00 (noon) via launchd.

## Open Questions

1. HTML report with interactive filtering - worth the complexity?
2. Integration with cloud storage (OneDrive, iCloud) - detect files that are cloud-only vs local?

---

## Future Enhancements

- Deduplication detection
- Time machine snapshot analysis
- Docker image/container cleanup
- Homebrew cache cleanup (`brew cleanup --dry-run`)
- npm/pip/cargo global cache analysis
- Spotlight index size monitoring

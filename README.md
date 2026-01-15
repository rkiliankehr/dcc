# DCC - Disk Cleanup Consultant

A fast, interactive disk cleanup tool for macOS. Scans your filesystem for space hogs and lets you clean them up with a friendly TUI.

## Features

- **Fast scanning** - Uses `fd` (Rust-based find) for 12x faster file discovery
- **Smart detection** - Finds build artifacts, unused apps, large files, caches, and more
- **Interactive TUI** - Review findings, cycle through actions, batch execute
- **Safe by default** - Preview commands before execution, snooze items for later
- **Scheduled scans** - Cron integration to keep recommendations fresh

## Screenshot

```
┌─ DCC - Disk Cleanup Consultant ──────────────────────────────────────────────┐
│ ▸ ○ ~/ws/old-project/node_modules          4.2 GB  delete   152 days stale   │
│   ○ ~/Library/Developer/CoreSimulator      3.8 GB  delete   cache            │
│   ○ ~/.ollama/models/llama2:13b            2.1 GB  ollama   model            │
│   ○ ~/Downloads/ubuntu-24.04.iso           1.9 GB  delete   installer        │
│   ● ~/ws/legacy-api/target                 1.2 GB  delete   45 days stale    │
├──────────────────────────────────────────────────────────────────────────────┤
│ Target: ~/ws/old-project/node_modules                                        │
│ Size: 4.2 GB (48,293 files)                                                  │
│ Action: delete → rm -rf '~/ws/old-project/node_modules'                      │
│ Restore: npm install                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
 ↑↓ navigate  ←→ cycle action  Space mark  Enter execute  i inspect  z snooze
```

## Installation

### Prerequisites

- Python 3.10+
- [fd](https://github.com/sharkdp/fd) - Fast file finder (optional but recommended)

```bash
brew install fd  # macOS
```

### Install DCC

```bash
git clone https://github.com/yourusername/dcc.git
cd dcc
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
make install  # Creates ~/bin/dcc symlink
```

### Optional: Scheduled Scans

```bash
make cron-install  # Runs at 9am, 12pm, 3pm daily
```

## Usage

```bash
dcc              # Scan (if stale) and open TUI
dcc scan         # Run scanner only
dcc tui          # Open TUI without scanning
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate findings |
| `←` `→` | Cycle through actions (delete, compress, git-gc, etc.) |
| `Space` | Mark/unmark for execution |
| `Enter` | Execute marked items |
| `i` | Inspect (reveal in Finder) |
| `z` | Snooze for 14 days |
| `q` | Quit |

## What It Finds

| Category | Examples | Actions |
|----------|----------|---------|
| Build artifacts | `node_modules`, `target/`, `.venv/` | delete |
| Large files | ISOs, DMGs, videos >1GB | delete, compress |
| Git repos | Repos with many loose objects | git-gc |
| Applications | Apps unused >90 days | delete |
| Caches | Browser, Xcode, npm caches | delete |
| Ollama models | Downloaded LLM models | ollama rm |
| Huggingface models | Cached transformer models | delete |

## Configuration

DCC stores its data in `~/.dcc/`:

```
~/.dcc/
├── scan.json        # Latest scan results
├── snoozed.json     # Snoozed items (14-day expiration)
└── state/           # Per-phase scan cache
```

## How It Works

1. **Scanner** (`dcc-scout.py`) - Crawls filesystem using `fd`, categorizes findings, estimates reclaimable space
2. **TUI** (`cleanup-tui.py`) - Displays findings sorted by size, lets you pick actions
3. **Executor** - Shows exact commands, asks for confirmation, runs them

## License

MIT

## Contributing

PRs welcome! Please open an issue first to discuss major changes.

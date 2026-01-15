#!/usr/bin/env python3
"""DCC - Disk Cleanup Consultant. Textual-based TUI for reviewing and executing disk cleanup actions."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Static, ListItem, ListView, Label
from textual.containers import Horizontal, Vertical, Container
from textual.binding import Binding
from textual.screen import Screen
from rich.text import Text
from rich.table import Table
from rich.console import Console
from datetime import datetime, timedelta

# DCC state directory
DCC_DIR = Path.home() / ".dcc"
SNOOZE_DAYS = 14  # Default snooze duration


def load_snoozed() -> dict:
    """Load snoozed targets from disk. Returns {target: expires_at}."""
    snoozed_path = DCC_DIR / "snoozed.json"
    if not snoozed_path.exists():
        return {}

    try:
        with open(snoozed_path) as f:
            data = json.load(f)

        now = datetime.now()
        active = {}
        for item in data.get("items", []):
            expires = datetime.fromisoformat(item["expires_at"])
            if expires > now:
                active[item["target"]] = item["expires_at"]
        return active
    except (json.JSONDecodeError, KeyError):
        return {}


def save_snoozed(snoozed: dict) -> None:
    """Save snoozed targets to disk. Input: {target: expires_at}."""
    DCC_DIR.mkdir(parents=True, exist_ok=True)
    snoozed_path = DCC_DIR / "snoozed.json"

    items = [{"target": t, "expires_at": e} for t, e in snoozed.items()]
    with open(snoozed_path, "w") as f:
        json.dump({"items": items}, f, indent=2)


def add_snooze(target: str, days: int = SNOOZE_DAYS) -> dict:
    """Add a snooze for a target. Returns updated snoozed dict."""
    snoozed = load_snoozed()
    expires = (datetime.now() + timedelta(days=days)).isoformat()
    snoozed[target] = expires
    save_snoozed(snoozed)
    return snoozed


def remove_snooze(target: str) -> dict:
    """Remove a snooze for a target. Returns updated snoozed dict."""
    snoozed = load_snoozed()
    snoozed.pop(target, None)
    save_snoozed(snoozed)
    return snoozed


def target_exists(target: str) -> bool:
    """Check if a target still exists on disk."""
    if target.startswith("ollama:"):
        # ollama:model:tag - check via ollama list
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return True  # Assume exists if we can't check
            # Parse output: "NAME    ID    SIZE    MODIFIED"
            model_tag = target[7:]  # Remove "ollama:" prefix
            for line in result.stdout.strip().split("\n")[1:]:  # Skip header
                if line.split()[0] == model_tag:
                    return True
            return False
        except Exception:
            return True  # Assume exists if error
    elif target.startswith("huggingface:"):
        # huggingface:org/model - check hub cache path
        model_id = target[12:]  # Remove "huggingface:" prefix
        cache_name = f"models--{model_id.replace('/', '--')}"
        cache_path = Path.home() / ".cache" / "huggingface" / "hub" / cache_name
        return cache_path.exists()
    else:
        # Regular path
        path = Path(target).expanduser()
        return path.exists()


# Category labels
CATEGORIES = {
    "app": "App", "node": "Node", "rust": "Rust", "venv": "Venv", "model": "Model",
    "cache": "Cache", "logs": "Logs", "git": "Git", "backup": "Backup", "archive": "Archiv",
    "orphan": "Orphan", "file": "File", "data": "Data", "ollama": "Ollama", "dotnet": ".NET",
    "java": "Java", "go": "Go", "swift": "Swift", "ios": "iOS", "huggingface": "HF",
}

# Action abbreviations (max 4 chars, lowercase like verbs)
ACTIONS = {
    "delete": "del",
    "compress": "zip",
    "git-gc": "gc",
    "ollama-rm": "rm",
    "hf-delete": "del",
    "restore": "rst",
    "skip": "skip",
}


def shorten_path(path: str, max_len: int = 45) -> str:
    """Shorten path in the middle if too long."""
    if len(path) <= max_len:
        return path
    # Keep start and end, ellipsis in middle
    keep = (max_len - 3) // 2
    return path[:keep] + "..." + path[-keep:]


class FindingItem(ListItem):
    """A single finding in the list."""

    def __init__(self, finding: dict, index: int) -> None:
        super().__init__()
        self.finding = finding
        self.index = index
        self.selected_action = finding.get("recommendation", "delete")
        self.marked = False
        self.snoozed = False

    def compose(self) -> ComposeResult:
        yield Static(id="content")

    def on_mount(self) -> None:
        self.update_display()

    def update_display(self) -> None:
        f = self.finding
        cat = CATEGORIES.get(f["category"], "???")
        size = f["size_human"].replace(" ", "")  # Remove space: "12.1 GB" -> "12.1GB"
        days = f["staleness_days"]
        action = ACTIONS.get(self.selected_action, "???")

        days_str = f"{days}d" if days < 1000 else "999+"

        # Calculate available width for path
        try:
            term_width = self.app.size.width if self.app else 80
        except Exception:
            term_width = 80
        # Columns: marker(3) + size(8) + cat(7) + action(5) + days(6) + spaces(6) + scrollbar(2) = ~37
        path_width = max(20, term_width - 40)
        target = shorten_path(f["target"], path_width)

        # Build row content - use Rich Text object for reliable background
        # Use ASCII marker to avoid Unicode width issues
        # Markers: * = marked, z = snoozed, space = normal
        if self.marked:
            marker = " * "
        elif self.snoozed:
            marker = " z "
        else:
            marker = "   "

        content = f"{marker} {size:>7} {cat:<6} {action:<4}  {days_str:>5}  {target}"

        if self.marked:
            # Use Rich Text with style for reliable orange background
            styled = Text(content)
            styled.stylize("black on rgb(255,140,0)")
            self.query_one("#content", Static).update(styled)
        elif self.snoozed:
            # Snoozed: dim with strikethrough effect
            styled = Text(content)
            styled.stylize("dim strike")
            self.query_one("#content", Static).update(styled)
        else:
            # Normal styling with markup - spacing must match marked format exactly
            text = f"    [cyan]{size:>7}[/] {cat:<6} [yellow]{action:<4}[/]  [dim]{days_str:>5}[/]  {target}"
            self.query_one("#content", Static).update(text)

    def toggle_mark(self) -> None:
        self.marked = not self.marked
        if self.marked:
            self.snoozed = False  # Can't be both marked and snoozed
        self.update_display()

    def toggle_snooze(self) -> None:
        self.snoozed = not self.snoozed
        if self.snoozed:
            self.marked = False  # Can't be both marked and snoozed
        self.update_display()

    def set_action(self, action: str) -> None:
        self.selected_action = action
        self.update_display()


class ActionSelector(Screen):
    """Screen for selecting an action for a finding."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("left", "go_back", "Back", show=False, priority=True),
        Binding("enter", "select_action", "Apply", priority=True),
        Binding("i", "inspect", "Inspect", priority=True),
    ]

    # Don't inherit bindings from parent app
    INHERIT_BINDINGS = False

    def __init__(self, finding: dict, current_action: str, callback) -> None:
        super().__init__()
        self.finding = finding
        self.current_action = current_action
        self.callback = callback

    def compose(self) -> ComposeResult:
        f = self.finding
        cat = CATEGORIES.get(f["category"], "???")

        with Container(id="action-container"):
            # Target info - compact
            yield Static(
                f"[bold cyan]{f['target']}[/]\n"
                f"[dim]{f['size_human']} • {cat} • {f['staleness_days']}d stale[/]",
                id="target-info"
            )

            # Details - compact horizontal
            details = []
            if f.get("file_count", 1) > 1:
                details.append(f"{f['file_count']:,} files")
            if f.get("last_modified"):
                details.append(f"Mod: {f['last_modified'][:10]}")
            if f.get("parent_project"):
                details.append(f"Project: {f['parent_project']}")
            if f.get("loose_objects"):
                details.append(f"{f['loose_objects']:,} loose objects")

            yield Static(f"[dim]{' • '.join(details)}[/]", id="details")
            yield Static(f"[dim]Restore: {f.get('reason', 'N/A')}[/]", id="restore")
            yield Static("")

            # Action list
            yield Static("[bold]Select Action:[/]", id="action-header")
            yield ListView(id="action-list")

        # Custom nav bar - magenta/violet spectrum like Claude
        yield Static("[bold magenta]↑↓[/] Navigate  [bold magenta]enter[/] Apply  [bold magenta]i[/] Inspect  [bold magenta]esc/←[/] Back", id="action-nav")

    def on_mount(self) -> None:
        action_list = self.query_one("#action-list", ListView)
        options = self.finding.get("options", [])

        # Full action names for the selector (lowercase verbs)
        action_names = {
            "delete": "delete",
            "compress": "compress (zip)",
            "git-gc": "git gc",
            "restore": "restore",
            "skip": "skip",
        }

        for i, opt in enumerate(options):
            action_id = opt["id"]
            reclaim_gb = opt.get("reclaim_bytes", 0) / 1e9
            reversible = "reversible" if opt.get("reversible") else "permanent"
            display = action_names.get(action_id, action_id)

            is_recommended = action_id == self.finding.get("recommendation")
            is_current = action_id == self.current_action

            rec_badge = " [yellow](rec)[/]" if is_recommended else ""
            cur_badge = " [green]◀[/]" if is_current else ""

            item = ListItem(
                Static(f"{display:<18} [dim]{reclaim_gb:.1f} GB • {reversible}[/]{rec_badge}{cur_badge}"),
                id=f"action-{action_id}"
            )
            item.action_id = action_id
            action_list.append(item)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_inspect(self) -> None:
        """Open Finder at the target location."""
        target = self.finding["target"]
        path = Path(target).expanduser()
        subprocess.run(["open", "-R", str(path)], check=False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection via Enter or click."""
        if hasattr(event.item, "action_id"):
            self.callback(event.item.action_id)
            self.app.pop_screen()

    def action_select_action(self) -> None:
        action_list = self.query_one("#action-list", ListView)
        if action_list.highlighted_child and hasattr(action_list.highlighted_child, "action_id"):
            self.callback(action_list.highlighted_child.action_id)
            self.app.pop_screen()


def _needs_sudo(target: str) -> bool:
    """Check if a path likely needs sudo to modify."""
    # Expand ~ for checking
    if target.startswith("~"):
        expanded = str(Path.home()) + target[1:]
    else:
        expanded = target

    # Paths in user's home directory don't need sudo
    home = str(Path.home())
    if expanded.startswith(home):
        return False

    # Virtual paths (ollama:, huggingface:) don't need sudo check
    if ":" in target and not target.startswith("/"):
        return False

    # System paths need sudo
    system_paths = ["/Applications", "/Library", "/usr", "/opt", "/var", "/private"]
    for sp in system_paths:
        if expanded.startswith(sp):
            return True

    return False


def _get_command_for_action(finding: dict, action: str) -> tuple[str, bool]:
    """Generate the actual shell command for an action.

    Returns: (command, needs_sudo)
    """
    target = finding["target"]
    sudo = _needs_sudo(target)
    prefix = "sudo " if sudo else ""

    if action == "ollama-rm":
        # ollama:model:tag -> ollama rm model:tag
        model_tag = target.replace("ollama:", "")
        return f"ollama rm {model_tag}", False
    elif action == "hf-delete":
        # huggingface:org/model -> rm -rf ~/.cache/huggingface/hub/models--org--model
        model_name = target.replace("huggingface:", "")
        org, name = model_name.split("/", 1) if "/" in model_name else ("", model_name)
        hf_dir = f"~/.cache/huggingface/hub/models--{org}--{name}"
        return f"rm -rf {hf_dir}", False
    elif action == "git-gc":
        # ~/.../repo/.git -> git -C ~/.../repo gc
        repo_dir = target.replace("/.git", "")
        return f"{prefix}git -C {repo_dir} gc --aggressive --prune=now", sudo
    elif action == "compress":
        return f"{prefix}zip -r {target}.zip {target} && {prefix}rm -rf {target}", sudo
    else:
        # Default: rm -rf
        return f"{prefix}rm -rf {target}", sudo


class ConfirmScreen(Screen):
    """Confirmation screen before execution."""

    BINDINGS = [
        Binding("ctrl+y", "confirm", "Execute", priority=True),
        Binding("n", "cancel", "No", priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    INHERIT_BINDINGS = False

    def __init__(self, items: list) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static("[bold red]CONFIRM EXECUTION[/]\n", id="confirm-header")

            # Group by action type
            rm_items = []      # rm -rf (regular deletes)
            ollama_items = []  # ollama rm
            hf_items = []      # huggingface deletes
            git_items = []     # git gc
            zip_items = []     # compress

            for item in self.items:
                action = item.selected_action
                if action == "ollama-rm":
                    ollama_items.append(item)
                elif action == "hf-delete":
                    hf_items.append(item)
                elif action == "git-gc":
                    git_items.append(item)
                elif action == "compress":
                    zip_items.append(item)
                elif "delete" in action:
                    rm_items.append(item)

            content = []
            total_gb = 0
            has_sudo = False

            def add_item(item, savings_override=None):
                nonlocal total_gb, has_sudo
                f = item.finding
                cmd, needs_sudo = _get_command_for_action(f, item.selected_action)
                if needs_sudo:
                    has_sudo = True
                    cmd_display = f"[bold yellow]{cmd}[/]"
                else:
                    cmd_display = f"[dim]{cmd}[/]"

                if savings_override is not None:
                    size_str = f"~{savings_override:.1f} GB"
                    total_gb += savings_override
                else:
                    size_str = f['size_human']
                    total_gb += f["size_bytes"] / 1e9

                content.append(f"  [cyan]{size_str:>8}[/]  {cmd_display}")

            # rm -rf section
            if rm_items:
                content.append("[bold red]rm -rf[/] [dim](permanent delete)[/]")
                content.append("")
                for item in rm_items:
                    add_item(item)
                content.append("")

            # ollama rm section
            if ollama_items:
                content.append("[bold red]ollama rm[/] [dim](remove model)[/]")
                content.append("")
                for item in ollama_items:
                    add_item(item)
                content.append("")

            # huggingface section
            if hf_items:
                content.append("[bold red]rm -rf[/] [dim](huggingface cache)[/]")
                content.append("")
                for item in hf_items:
                    add_item(item)
                content.append("")

            # git gc section
            if git_items:
                content.append("[bold yellow]git gc[/] [dim](compress git history)[/]")
                content.append("")
                for item in git_items:
                    f = item.finding
                    savings = f.get("gc_potential_bytes", f["size_bytes"] * 0.2) / 1e9
                    add_item(item, savings_override=savings)
                content.append("")

            # zip section
            if zip_items:
                content.append("[bold green]zip[/] [dim](compress, reversible)[/]")
                content.append("")
                for item in zip_items:
                    f = item.finding
                    savings = f["size_bytes"] * 0.7 / 1e9  # ~70% compression
                    add_item(item, savings_override=savings)
                content.append("")

            # Summary
            content.append(f"[dim]{'─' * 60}[/]")
            content.append(f"[bold]ESTIMATED SAVINGS: [green]{total_gb:.1f} GB[/][/]")

            if has_sudo:
                content.append("")
                content.append("[bold yellow]⚠ Some commands require sudo (highlighted)[/]")

            yield Static("\n".join(content), id="confirm-content")

        yield Static("[bold green]^y[/] Execute  [bold magenta]n/esc[/] Cancel", id="confirm-nav")

    def action_confirm(self) -> None:
        self.app.exit(result=self.items)

    def action_cancel(self) -> None:
        self.app.pop_screen()


class CleanupApp(App):
    """DCC - Disk Cleanup Consultant main application."""

    CSS = """
    Screen {
        background: #1e1e1e;
    }

    #main-container {
        height: 100%;
    }

    #status-bar {
        height: 2;
        padding: 0 1;
        background: #252525;
    }

    #separator-top {
        height: 1;
        padding: 0 1;
        color: #404040;
    }

    #findings-list {
        height: 1fr;
        background: transparent;
        scrollbar-background: #2d2d2d;
        scrollbar-color: #606060;
        scrollbar-color-hover: #808080;
        scrollbar-color-active: #a0a0a0;
    }

    #details-panel {
        height: 8;
        background: #252525;
        border-top: solid #404040;
        padding: 0 1;
    }

    #action-container {
        padding: 1 2;
    }

    #target-info {
        margin-bottom: 1;
    }

    #action-list {
        height: auto;
        max-height: 10;
        margin-top: 1;
    }

    #confirm-container {
        padding: 1 2;
    }

    #action-nav, #confirm-nav, #main-nav {
        dock: bottom;
        height: 1;
        background: #252525;
        padding: 0 1;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem:hover {
        background: #2a2a2a;
    }

    ListItem.--highlight {
        background: #37373d;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("left", "prev_action", "←", priority=True),
        Binding("right", "next_action", "→", priority=True),
        Binding("space", "toggle_mark", "Mark", priority=True),
        Binding("s", "toggle_hide_skip", "Hide Skip"),
        Binding("z", "toggle_snooze", "Snooze"),
        Binding("i", "inspect", "Inspect"),
        Binding("ctrl+x", "execute", "Execute", priority=True),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, findings_file: str) -> None:
        super().__init__()
        self.findings_file = findings_file
        self.findings_data = None
        self.findings = []  # Sorted findings list
        self.item_state = {}  # State by index: {idx: {marked, snoozed, selected_action}}
        self.hide_skip = False  # Toggle to hide items with "skip" recommendation
        self.initially_snoozed = set()  # Targets that were snoozed on load

    def compose(self) -> ComposeResult:
        with Vertical(id="main-container"):
            yield Static(id="status-bar")
            yield Static("[dim]" + "─" * 200 + "[/]", id="separator-top")
            yield ListView(id="findings-list")
            yield Static(id="details-panel")

        # Custom nav bar - magenta/violet spectrum, execute highlighted on right
        yield Static(id="main-nav")

    def on_mount(self) -> None:
        # Load findings
        with open(self.findings_file) as f:
            self.findings_data = json.load(f)

        # Sort by size descending
        self.findings = sorted(
            self.findings_data["findings"],
            key=lambda x: x["size_bytes"],
            reverse=True
        )

        # Load persisted snoozes
        snoozed_targets = load_snoozed()
        self.initially_snoozed = set(snoozed_targets.keys())

        # Initialize state for all items, checking if targets still exist
        cleaned_count = 0
        for i, finding in enumerate(self.findings):
            target = finding["target"]
            is_cleaned = not target_exists(target)
            if is_cleaned:
                cleaned_count += 1
            self.item_state[i] = {
                "marked": False,
                "snoozed": target in snoozed_targets,
                "cleaned": is_cleaned,
                "selected_action": finding.get("recommendation", "delete"),
            }

        if cleaned_count > 0:
            self.notify(f"{cleaned_count} item(s) already cleaned")

        # Populate visible list
        self.refresh_list()
        self.update_status()
        self.update_details()
        self.update_nav()

    def update_nav(self) -> None:
        """Update navigation bar to reflect current state."""
        nav = self.query_one("#main-nav", Static)
        skip_label = "[reverse]s Hide Skip[/]" if self.hide_skip else "s Hide Skip"
        nav.update(
            f"[bold magenta]↑↓[/] Navigate  "
            f"[bold magenta]←→[/] Action  "
            f"[bold magenta]space[/] Mark  "
            f"[bold magenta]{skip_label}[/]  "
            f"[bold magenta]z[/] Snooze  "
            f"[bold magenta]i[/] Inspect  "
            f"[bold magenta]^q[/] Quit  "
            f"[bold rgb(0,255,0)]^x Execute[/]"
        )

    def refresh_list(self) -> None:
        """Rebuild the list with only visible items."""
        findings_list = self.query_one("#findings-list", ListView)
        findings_list.clear()

        has_items = False
        # Create new widgets for visible items, restoring state
        for i, finding in enumerate(self.findings):
            state = self.item_state[i]
            # Hide cleaned items (already deleted)
            if state.get("cleaned"):
                continue
            # Hide snoozed items
            if state["snoozed"]:
                continue
            # Hide skip items if toggle is on
            if self.hide_skip and finding.get("recommendation") == "skip":
                continue

            item = FindingItem(finding, i)
            # Restore state
            item.marked = state["marked"]
            item.snoozed = state["snoozed"]
            item.selected_action = state["selected_action"]
            findings_list.append(item)
            has_items = True

        # Select first item - track ourselves since children may not be populated yet
        if has_items:
            findings_list.index = 0
            findings_list.refresh()

    def update_status(self) -> None:
        total_count = len(self.findings)
        marked_count = sum(1 for s in self.item_state.values() if s["marked"])
        marked_gb = sum(self.findings[i]["size_bytes"] / 1e9 for i, s in self.item_state.items() if s["marked"])
        snoozed_count = sum(1 for s in self.item_state.values() if s["snoozed"])
        cleaned_count = sum(1 for s in self.item_state.values() if s.get("cleaned"))
        skip_count = sum(1 for f in self.findings if f.get("recommendation") == "skip")
        # Visible = total - snoozed - cleaned - (skip if hidden)
        hidden_skip = skip_count if self.hide_skip else 0
        visible_count = total_count - snoozed_count - cleaned_count - hidden_skip
        # Total GB excludes cleaned items (only actionable space)
        total_gb = sum(self.findings[i]["size_bytes"] for i, s in self.item_state.items() if not s.get("cleaned")) / 1e9

        # Get disk space info
        disk = shutil.disk_usage(Path.home())
        disk_free_gb = disk.free / 1e9
        disk_used_pct = (disk.used / disk.total) * 100

        # Calculate padding for right-aligned disk info
        try:
            term_width = self.app.size.width if self.app else 80
        except Exception:
            term_width = 80

        status = self.query_one("#status-bar", Static)
        snoozed_part = f"  [dim]Snoozed:[/] [dim]{snoozed_count}[/]" if snoozed_count > 0 else ""
        hidden_part = f"  [dim]Hidden:[/] [dim]{skip_count}[/]" if self.hide_skip else ""

        # Line 1
        line1_left = "DCC Disk Cleanup Consultant"
        line1_right = f"{disk_free_gb:.0f} GB free ({disk_used_pct:.0f}% used)"
        line1_pad = term_width - len(line1_left) - len(line1_right) - 2
        line1 = f"[bold magenta]DCC[/] [dim]Disk Cleanup Consultant[/]{' ' * line1_pad}[green]{disk_free_gb:.0f} GB[/] free ({disk_used_pct:.0f}% used)"

        # Line 2
        line2_left_plain = f"Showing: {visible_count} ({total_gb:.1f} GB)"
        line2_right_plain = f"Selected: {marked_count} ({marked_gb:.1f} GB)"
        line2_pad = term_width - len(line2_left_plain) - len(line2_right_plain) - 2
        line2 = f"[dim]Showing:[/] [cyan]{visible_count}[/] ([cyan]{total_gb:.1f} GB[/]){' ' * line2_pad}[dim]Selected:[/] [rgb(255,140,0)]{marked_count}[/] ([rgb(255,140,0)]{marked_gb:.1f} GB[/])"

        status.update(f"{line1}\n{line2}")

    def update_details(self) -> None:
        findings_list = self.query_one("#findings-list", ListView)
        details = self.query_one("#details-panel", Static)

        if findings_list.highlighted_child and isinstance(findings_list.highlighted_child, FindingItem):
            f = findings_list.highlighted_child.finding
            action = findings_list.highlighted_child.selected_action
            cat = CATEGORIES.get(f["category"], "???")

            # Line 1: Target
            line1 = f"[bold cyan]{f['target']}[/]"

            # Line 2: Key stats - rainbow colors
            parts = [
                f"[cyan]{f['size_human']}[/]",
                f"[green]{cat}[/]",
                f"[yellow]{f['staleness_days']}d stale[/]",
            ]
            if f.get("file_count", 1) > 1:
                parts.append(f"[magenta]{f['file_count']:,} files[/]")
            if f.get("last_modified"):
                parts.append(f"[blue]Modified {f['last_modified'][:10]}[/]")
            line2 = " • ".join(parts)

            # Line 3: Extra details
            extra = []
            if f.get("parent_project"):
                extra.append(f"[dim]Project:[/] {f['parent_project']}")
            if f.get("loose_objects"):
                extra.append(f"[dim]Loose objects:[/] {f['loose_objects']:,}")
            line3 = "  ".join(extra) if extra else ""

            # Line 4: Actions (←→ to cycle)
            opts = f.get("options", [])
            action_parts = []
            for opt in opts:
                opt_id = opt["id"]
                opt_name = ACTIONS.get(opt_id, opt_id)
                reclaim_gb = opt.get("reclaim_bytes", 0) / 1e9
                reversible = "rev" if opt.get("reversible") else "perm"
                is_selected = opt_id == action
                is_rec = opt_id == f.get("recommendation")

                if is_selected:
                    rec_label = " [green](recommended)[/]" if is_rec else ""
                    action_parts.append(f"[bold yellow]◀ {opt_name} ▶[/] [dim]({reclaim_gb:.1f}GB {reversible})[/]{rec_label}")
                else:
                    action_parts.append(f"[dim]{opt_name}[/]")
            line4 = "  ".join(action_parts)

            # Line 5: Context-appropriate label
            recommendation = f.get("recommendation", "delete")
            reason = f.get("reason", "N/A")
            if recommendation == "skip":
                line5 = f"[dim]Why skip:[/] [italic]{reason}[/]"
            else:
                line5 = f"[dim]Restore:[/] [italic]{reason}[/]"

            lines = [line1, line2]
            if line3:
                lines.append(line3)
            lines.extend([line4, line5])
            details.update("\n".join(lines))
        else:
            details.update("")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.update_details()

    def on_resize(self, event) -> None:
        """Refresh all items when terminal is resized."""
        findings_list = self.query_one("#findings-list", ListView)
        for child in findings_list.children:
            if isinstance(child, FindingItem):
                child.update_display()

    def on_key(self, event) -> None:
        """Handle key events directly for more reliable space handling."""
        if event.key == "space":
            event.prevent_default()
            event.stop()
            self.action_toggle_mark()

    def action_toggle_mark(self) -> None:
        findings_list = self.query_one("#findings-list", ListView)
        if findings_list.highlighted_child and isinstance(findings_list.highlighted_child, FindingItem):
            item = findings_list.highlighted_child
            item.toggle_mark()
            # Save state
            self.item_state[item.index]["marked"] = item.marked
            self.update_status()

    def action_toggle_snooze(self) -> None:
        findings_list = self.query_one("#findings-list", ListView)
        if findings_list.highlighted_child and isinstance(findings_list.highlighted_child, FindingItem):
            item = findings_list.highlighted_child
            item.toggle_snooze()
            # Save state in memory only - will persist on ctrl+x
            self.item_state[item.index]["snoozed"] = item.snoozed
            self.update_status()

    def _save_snoozes(self) -> None:
        """Persist all current snooze states to disk."""
        # Load existing snoozes (to preserve ones not in current view)
        snoozed = load_snoozed()

        # Update with current session's snooze states
        for i, state in self.item_state.items():
            target = self.findings[i]["target"]
            if state["snoozed"]:
                if target not in snoozed:
                    expires = (datetime.now() + timedelta(days=SNOOZE_DAYS)).isoformat()
                    snoozed[target] = expires
            else:
                # Remove if un-snoozed
                snoozed.pop(target, None)

        save_snoozed(snoozed)

    def action_toggle_hide_skip(self) -> None:
        """Toggle hiding of items with 'skip' recommendation."""
        self.hide_skip = not self.hide_skip
        self.refresh_list()
        self.update_nav()
        self.update_status()
        self.update_details()

    def _cycle_action(self, direction: int) -> None:
        """Cycle through actions for current item. direction: 1=next, -1=prev"""
        findings_list = self.query_one("#findings-list", ListView)
        if findings_list.highlighted_child and isinstance(findings_list.highlighted_child, FindingItem):
            item = findings_list.highlighted_child
            opts = item.finding.get("options", [])
            if not opts:
                return
            # Find current action index
            current_ids = [o["id"] for o in opts]
            try:
                idx = current_ids.index(item.selected_action)
            except ValueError:
                idx = 0
            # Cycle
            new_idx = (idx + direction) % len(opts)
            item.set_action(opts[new_idx]["id"])
            # Save state
            self.item_state[item.index]["selected_action"] = item.selected_action
            self.update_details()

    def action_prev_action(self) -> None:
        self._cycle_action(-1)

    def action_next_action(self) -> None:
        self._cycle_action(1)

    def action_inspect(self) -> None:
        """Open Finder at the target location."""
        findings_list = self.query_one("#findings-list", ListView)
        if findings_list.highlighted_child and isinstance(findings_list.highlighted_child, FindingItem):
            target = findings_list.highlighted_child.finding["target"]
            # Expand ~ to home directory
            path = Path(target).expanduser()
            # Use open -R to reveal in Finder (works for files and directories)
            subprocess.run(["open", "-R", str(path)], check=False)

    def action_execute(self) -> None:
        # Find newly snoozed items (not in initially_snoozed)
        newly_snoozed = []
        for i, state in self.item_state.items():
            target = self.findings[i]["target"]
            if state["snoozed"] and target not in self.initially_snoozed:
                newly_snoozed.append(self.findings[i])

        # Save snoozes to disk
        self._save_snoozes()

        # Build list of marked items from state
        class MarkedItem:
            def __init__(self, finding, selected_action):
                self.finding = finding
                self.selected_action = selected_action

        marked = []
        for i, state in self.item_state.items():
            if state["marked"]:
                marked.append(MarkedItem(self.findings[i], state["selected_action"]))

        if marked:
            self.push_screen(ConfirmScreen(marked))
        elif newly_snoozed:
            # Exit with details of newly snoozed items
            lines = [f"Snoozed {len(newly_snoozed)} item(s):"]
            for f in newly_snoozed:
                lines.append(f"  {f['size_human']:>8}  {f['target']}")
            self.exit(message="\n".join(lines))
        else:
            self.notify("No items marked or snoozed", timeout=2)


def execute_actions(items: list) -> dict:
    """Execute the cleanup actions and return results."""
    results = {
        "success": [],
        "failed": [],
        "total_reclaimed": 0,
    }

    print("\n\033[1mExecuting cleanup actions...\033[0m\n")

    for i, item in enumerate(items, 1):
        finding = item.finding
        action = item.selected_action
        target = finding["target"]
        size = finding["size_bytes"]

        cmd, needs_sudo = _get_command_for_action(finding, action)

        # Progress indicator
        print(f"[{i}/{len(items)}] {cmd}")

        try:
            # Build the actual command to run
            if action == "ollama-rm":
                # ollama rm model:tag
                model_tag = target.replace("ollama:", "")
                result = subprocess.run(
                    ["ollama", "rm", model_tag],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
            elif action == "hf-delete":
                # rm -rf huggingface cache dir
                model_name = target.replace("huggingface:", "")
                org, name = model_name.split("/", 1) if "/" in model_name else ("", model_name)
                hf_dir = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{org}--{name}"
                if hf_dir.exists():
                    shutil.rmtree(hf_dir)
                result = subprocess.CompletedProcess(args=[], returncode=0)
            elif action == "git-gc":
                # git gc --aggressive --prune=now
                repo_dir = target.replace("/.git", "")
                repo_path = Path(repo_dir).expanduser()
                shell_cmd = ["git", "-C", str(repo_path), "gc", "--aggressive", "--prune=now"]
                if needs_sudo:
                    shell_cmd = ["sudo"] + shell_cmd
                result = subprocess.run(
                    shell_cmd,
                    capture_output=True,
                    text=True,
                    timeout=600
                )
            elif action == "compress":
                # zip -r target.zip target && rm -rf target
                target_path = Path(target).expanduser()
                zip_path = target_path.with_suffix(target_path.suffix + ".zip")
                # Create zip
                result = subprocess.run(
                    ["zip", "-r", "-q", str(zip_path), str(target_path)],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if result.returncode == 0:
                    # Remove original
                    shutil.rmtree(target_path)
            else:
                # Default: rm -rf
                target_path = Path(target).expanduser()
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path)
                    else:
                        target_path.unlink()
                result = subprocess.CompletedProcess(args=[], returncode=0)

            if result.returncode == 0:
                results["success"].append({
                    "target": target,
                    "action": action,
                    "size": size,
                    "cmd": cmd,
                })
                results["total_reclaimed"] += size
                print(f"       \033[32m✓\033[0m {finding['size_human']} reclaimed")
            else:
                results["failed"].append({
                    "target": target,
                    "action": action,
                    "cmd": cmd,
                    "error": result.stderr or "Unknown error",
                })
                print(f"       \033[31m✗\033[0m {result.stderr.strip() if result.stderr else 'Failed'}")

        except subprocess.TimeoutExpired:
            results["failed"].append({
                "target": target,
                "action": action,
                "cmd": cmd,
                "error": "Command timed out",
            })
            print(f"       \033[31m✗\033[0m Timeout")
        except PermissionError as e:
            results["failed"].append({
                "target": target,
                "action": action,
                "cmd": cmd,
                "error": f"Permission denied: {e}",
            })
            print(f"       \033[31m✗\033[0m Permission denied (try with sudo?)")
        except Exception as e:
            results["failed"].append({
                "target": target,
                "action": action,
                "cmd": cmd,
                "error": str(e),
            })
            print(f"       \033[31m✗\033[0m {e}")

    return results


def print_summary(results: dict) -> None:
    """Print execution summary."""
    print("\n" + "═" * 50)
    print("\033[1mEXECUTION SUMMARY\033[0m")
    print("═" * 50)

    total_gb = results["total_reclaimed"] / 1e9
    success_count = len(results["success"])
    failed_count = len(results["failed"])

    print(f"\n\033[32m✓ Successful:\033[0m {success_count} actions")
    if results["success"]:
        print(f"  \033[32mReclaimed: {total_gb:.2f} GB\033[0m")

    if results["failed"]:
        print(f"\n\033[31m✗ Failed:\033[0m {failed_count} actions")
        for f in results["failed"]:
            print(f"  • {f['target']}")
            print(f"    {f['error']}")

    print()


def main():
    findings_file = sys.argv[1] if len(sys.argv) > 1 else "sample-findings.json"

    if not Path(findings_file).exists():
        print(f"Error: {findings_file} not found")
        sys.exit(1)

    app = CleanupApp(findings_file)
    result = app.run()

    if isinstance(result, str):
        # Message from exit (e.g., snooze saved)
        print(f"\n{result}")
    elif result:
        # Execute the actions
        results = execute_actions(result)
        print_summary(results)

        # Return exit code based on results
        if results["failed"]:
            sys.exit(1)


if __name__ == "__main__":
    main()

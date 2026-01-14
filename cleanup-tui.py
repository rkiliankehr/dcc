#!/usr/bin/env python3
"""DCC - Disk Cleanup Consultant. Textual-based TUI for reviewing and executing disk cleanup actions."""
import json
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

# Category labels
CATEGORIES = {
    "app": "App", "node": "Node", "rust": "Rust", "venv": "Venv", "model": "Model",
    "cache": "Cache", "logs": "Logs", "git": "Git", "backup": "Backup", "archive": "Archiv",
    "orphan": "Orphan", "file": "File", "data": "Data"
}

# Action abbreviations (3 chars max, lowercase like verbs)
ACTIONS = {
    "delete": "del",
    "compress": "zip",
    "git-gc": "gc",
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
        path_width = max(20, term_width - 36)  # account for wider category column
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


class ConfirmScreen(Screen):
    """Confirmation screen before execution."""

    BINDINGS = [
        Binding("y", "confirm", "Yes", priority=True),
        Binding("n", "cancel", "No", priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    INHERIT_BINDINGS = False

    def __init__(self, items: list) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static("[bold]Confirm Actions[/]\n", id="confirm-header")

            # Group by action
            deletes = [(i.finding, i.selected_action) for i in self.items if "delete" in i.selected_action]
            compresses = [(i.finding, i.selected_action) for i in self.items if "compress" in i.selected_action]
            git_gcs = [(i.finding, i.selected_action) for i in self.items if "git-gc" in i.selected_action]

            content = []
            total = 0

            if deletes:
                content.append("[bold red]🗑️  DELETE (permanent):[/]")
                del_total = 0
                for f, _ in deletes:
                    content.append(f"    {f['size_human']:>8}  {f['target']}")
                    del_total += f["size_bytes"] / 1e9
                content.append(f"    [dim]{'─' * 40}[/]")
                content.append(f"    [bold]{del_total:.1f} GB[/]")
                content.append("")
                total += del_total

            if compresses:
                content.append("[bold green]📦 COMPRESS (reversible):[/]")
                comp_total = 0
                for f, _ in compresses:
                    content.append(f"    {f['size_human']:>8}  {f['target']}")
                    comp_total += f["size_bytes"] / 1e9
                savings = comp_total * 0.8
                content.append(f"    [dim]{'─' * 40}[/]")
                content.append(f"    [bold]~{savings:.1f} GB[/] savings")
                content.append("")
                total += savings

            if git_gcs:
                content.append("[bold magenta]🔧 GIT GC:[/]")
                for f, _ in git_gcs:
                    content.append(f"    {f['size_human']:>8}  {f['target']}")
                content.append("")

            content.append(f"[bold]{'═' * 50}[/]")
            content.append(f"[bold]ESTIMATED SAVINGS: [cyan]{total:.1f} GB[/][/]")

            yield Static("\n".join(content), id="confirm-content")

        # Custom nav bar - magenta/violet spectrum like Claude
        yield Static("[bold magenta]y[/] Execute  [bold magenta]n[/] Cancel", id="confirm-nav")

    def action_confirm(self) -> None:
        self.app.exit(result=self.items)

    def action_cancel(self) -> None:
        self.app.pop_screen()


class CleanupApp(App):
    """DCC - Disk Cleanup Consultant main application."""

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 100%;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
    }

    #separator-top {
        height: 1;
        padding: 0 1;
    }

    #findings-list {
        height: 1fr;
        background: transparent;
    }

    #details-panel {
        height: 8;
        background: transparent;
        border-top: solid gray;
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
        background: $primary-background;
        padding: 0 1;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem:hover {
        background: transparent;
    }

    ListItem.--highlight {
        background: cyan !important;
        color: black;
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

        # Initialize state for all items
        for i, finding in enumerate(self.findings):
            self.item_state[i] = {
                "marked": False,
                "snoozed": False,
                "selected_action": finding.get("recommendation", "delete"),
            }

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
            should_hide = self.hide_skip and finding.get("recommendation") == "skip"
            if should_hide:
                continue

            item = FindingItem(finding, i)
            # Restore state
            state = self.item_state[i]
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
        total_gb = sum(f["size_bytes"] for f in self.findings) / 1e9
        marked_count = sum(1 for s in self.item_state.values() if s["marked"])
        marked_gb = sum(self.findings[i]["size_bytes"] / 1e9 for i, s in self.item_state.items() if s["marked"])
        snoozed_count = sum(1 for s in self.item_state.values() if s["snoozed"])
        skip_count = sum(1 for f in self.findings if f.get("recommendation") == "skip")
        visible_count = total_count - skip_count if self.hide_skip else total_count

        status = self.query_one("#status-bar", Static)
        snoozed_part = f"  [dim]Snoozed:[/] [dim]{snoozed_count}[/]" if snoozed_count > 0 else ""
        hidden_part = f"  [dim]Hidden:[/] [dim]{skip_count}[/]" if self.hide_skip else ""
        status.update(
            f"[bold magenta]DCC[/] [dim italic]Disk Cleanup Consultant[/]  "
            f"[dim]Showing:[/] [cyan]{visible_count}[/] items ([cyan]{total_gb:.1f} GB[/])  "
            f"[dim]Selected:[/] [rgb(255,140,0)]{marked_count}[/] ([rgb(255,140,0)]{marked_gb:.1f} GB[/])"
            f"{snoozed_part}{hidden_part}"
        )

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
            # Save state
            self.item_state[item.index]["snoozed"] = item.snoozed
            self.update_status()

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


def main():
    findings_file = sys.argv[1] if len(sys.argv) > 1 else "sample-findings.json"

    if not Path(findings_file).exists():
        print(f"Error: {findings_file} not found")
        sys.exit(1)

    app = CleanupApp(findings_file)
    result = app.run()

    if result:
        print("\n[Prototype] Would execute:")
        for item in result:
            print(f"  {item.selected_action}: {item.finding['target']} ({item.finding['size_human']})")


if __name__ == "__main__":
    main()

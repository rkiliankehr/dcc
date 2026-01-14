#!/usr/bin/env python3
"""DCC Scout - Filesystem scanner for cleanup opportunities."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

# Default config
DEFAULT_CONFIG = {
    "scan_paths": ["~/"],
    "exclude_paths": [
        "~/Library/Mobile Documents",
        "~/.Trash",
        "~/Library/CloudStorage",
        "~/Library/Group Containers/UBF8T346G9.OneDriveStandaloneSuite",
        # VM and container runtimes (dangerous to delete)
        "~/.colima",
        "~/.lima",
        "~/.docker",
        "~/.orbstack",
        "~/.podman",
        "~/Library/Containers/com.docker.docker",
        "~/Library/Containers/com.utmapp.UTM",
        "~/Library/Containers/dev.kdrag0n.MacVirt",
        # Virtualization data
        "~/Virtual Machines.localized",
        "~/Parallels",
        "~/.vagrant.d/boxes",
    ],
    "thresholds": {
        "large_file_min_gb": 1,
        "stale_days": 30,
        "stale_app_days": 90,
        "log_file_min_mb": 100,
    },
    "phases": {
        "large_files": True,
        "build_artifacts": True,
        "git_repos": True,
        "applications": True,
        "library_leftovers": True,
        "caches": True,
        "logs": True,
    },
}

# Build artifact patterns: (marker_file, artifact_dirs, category, restore_hint)
BUILD_ARTIFACTS = [
    ("package.json", ["node_modules"], "node", "npm install"),
    ("package-lock.json", ["node_modules"], "node", "npm install"),
    ("yarn.lock", ["node_modules"], "node", "yarn install"),
    ("pnpm-lock.yaml", ["node_modules"], "node", "pnpm install"),
    ("Cargo.toml", ["target"], "rust", "cargo build"),
    ("pyproject.toml", [".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"], "venv", "uv sync / pip install"),
    ("setup.py", [".venv", "venv"], "venv", "pip install -e ."),
    ("requirements.txt", [".venv", "venv"], "venv", "pip install -r requirements.txt"),
    ("go.mod", ["vendor"], "go", "go mod vendor"),
    ("build.gradle", ["build", ".gradle"], "java", "gradle build"),
    ("pom.xml", ["target"], "java", "mvn package"),
    ("*.csproj", ["bin", "obj"], "dotnet", "dotnet build"),
    ("Package.swift", [".build"], "swift", "swift build"),
    ("Podfile", ["Pods"], "ios", "pod install"),
]

# Cache directories to scan (~/Library/Caches scanned by subdirectory separately)
CACHE_DIRS = [
    ("~/Library/Developer/CoreSimulator/Caches", "cache"),
    ("~/.cache", "cache"),
    ("~/.npm/_cacache", "cache"),
    ("~/.cargo/registry/cache", "cache"),
]

# Directories that indicate LLM models
MODEL_DIRS = [
    ("~/.ollama/models", "model"),
    ("~/.cache/huggingface", "model"),
    ("~/.cache/torch", "model"),
]


class DccScout:
    def __init__(self, config_path: Optional[Path] = None, dcc_dir: Optional[Path] = None):
        self.dcc_dir = Path(dcc_dir) if dcc_dir else Path.home() / ".dcc"
        self.state_dir = self.dcc_dir / "state"
        self.config_path = config_path or self.dcc_dir / "config.yaml"
        self.config = self._load_config()
        self.snoozed = self._load_snoozed()

    def _load_config(self) -> dict:
        """Load config from file or use defaults."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                user_config = yaml.safe_load(f) or {}
            # Merge with defaults
            config = DEFAULT_CONFIG.copy()
            for key, value in user_config.items():
                if isinstance(value, dict) and key in config:
                    config[key] = {**config[key], **value}
                else:
                    config[key] = value
            return config
        return DEFAULT_CONFIG.copy()

    def _load_snoozed(self) -> set:
        """Load snoozed targets as a set for quick lookup."""
        snoozed_path = self.dcc_dir / "snoozed.json"
        if not snoozed_path.exists():
            return set()

        try:
            with open(snoozed_path) as f:
                data = json.load(f)

            now = datetime.now()
            active = set()
            for item in data.get("items", []):
                expires = datetime.fromisoformat(item["expires_at"])
                if expires > now:
                    active.add(item["target"])
            return active
        except (json.JSONDecodeError, KeyError):
            return set()

    def _is_snoozed(self, target: str) -> bool:
        """Check if target is currently snoozed."""
        return target in self.snoozed

    def _is_excluded(self, path: Path) -> bool:
        """Check if path is in exclude list."""
        path_str = str(path)
        for excl in self.config["exclude_paths"]:
            excl_expanded = str(Path(excl).expanduser())
            if path_str.startswith(excl_expanded):
                return True
        return False

    def _is_cloud_only(self, path: Path) -> bool:
        """Check if file is a cloud-only placeholder (OneDrive, iCloud, Dropbox).

        Cloud-only files have apparent size (st_size) but use 0 or minimal disk blocks.
        Returns True if file uses <1% of its apparent size on disk.
        """
        try:
            stat = path.stat()
            apparent_size = stat.st_size
            # st_blocks is in 512-byte units
            actual_blocks = stat.st_blocks * 512

            # If file appears large (>1MB) but uses almost no disk space, it's cloud-only
            if apparent_size > 1_000_000 and actual_blocks < apparent_size * 0.01:
                return True
            return False
        except OSError:
            return False

    def _get_actual_size(self, path: Path) -> int:
        """Get actual disk usage (not apparent size) for a file."""
        try:
            stat = path.stat()
            # st_blocks is in 512-byte units
            return stat.st_blocks * 512
        except OSError:
            return 0

    def _get_staleness_days(self, path: Path) -> int:
        """Calculate days since last access."""
        try:
            stat = path.stat()
            atime = datetime.fromtimestamp(stat.st_atime)
            return (datetime.now() - atime).days
        except OSError:
            return 0

    def _get_mtime(self, path: Path) -> Optional[str]:
        """Get modification time as ISO string."""
        try:
            stat = path.stat()
            return datetime.fromtimestamp(stat.st_mtime).isoformat()
        except OSError:
            return None

    def _get_atime(self, path: Path) -> Optional[str]:
        """Get access time as ISO string."""
        try:
            stat = path.stat()
            return datetime.fromtimestamp(stat.st_atime).isoformat()
        except OSError:
            return None

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes as human readable."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"

    def _get_dir_size(self, path: Path) -> tuple[int, int]:
        """Get total actual disk usage and file count of directory.

        Uses st_blocks to get real disk usage, not apparent size.
        This correctly handles sparse files and cloud-only placeholders.
        """
        total_size = 0
        file_count = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    try:
                        stat = entry.stat()
                        # Use actual disk blocks, not apparent size
                        total_size += stat.st_blocks * 512
                        file_count += 1
                    except OSError:
                        pass
        except PermissionError:
            pass
        return total_size, file_count

    def _save_phase_result(self, phase: str, findings: list):
        """Save intermediate phase results."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.state_dir / f"{phase}.json"
        with open(output_path, "w") as f:
            json.dump({
                "phase": phase,
                "generated": datetime.now().isoformat(),
                "count": len(findings),
                "findings": findings,
            }, f, indent=2)
        print(f"  Saved {len(findings)} findings to {output_path}")

    def _load_phase_result(self, phase: str) -> Optional[list]:
        """Load existing phase results if available."""
        phase_path = self.state_dir / f"{phase}.json"
        if phase_path.exists():
            try:
                with open(phase_path) as f:
                    data = json.load(f)
                return data.get("findings", [])
            except json.JSONDecodeError:
                return None
        return None

    def _make_finding(self, target: str, category: str, size_bytes: int,
                      file_count: int, staleness_days: int, reason: str,
                      options: list, recommendation: str,
                      parent_project: Optional[str] = None,
                      **extra) -> dict:
        """Create a finding dict."""
        path = Path(target).expanduser()
        target_str = str(path).replace(str(Path.home()), "~")

        finding = {
            "target": target_str,
            "category": category,
            "size_bytes": size_bytes,
            "size_human": self._format_size(size_bytes),
            "file_count": file_count,
            "last_modified": self._get_mtime(path),
            "last_accessed": self._get_atime(path),
            "staleness_days": staleness_days,
            "is_archive": False,
            "options": options,
            "recommendation": recommendation,
            "reason": reason,
        }

        if parent_project:
            finding["parent_project"] = parent_project

        finding.update(extra)
        return finding

    # === PHASE: Large Files ===
    def scan_large_files(self) -> list:
        """Find files larger than threshold."""
        print("Scanning for large files...")
        findings = []
        min_bytes = self.config["thresholds"]["large_file_min_gb"] * 1e9

        for scan_path in self.config["scan_paths"]:
            root = Path(scan_path).expanduser()
            if not root.exists():
                continue

            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if self._is_excluded(path):
                    continue

                try:
                    stat = path.stat()
                    apparent_size = stat.st_size
                    if apparent_size < min_bytes:
                        continue

                    # Use actual disk usage, not apparent size
                    size = stat.st_blocks * 512

                    # Skip cloud-only placeholders (OneDrive, iCloud, Dropbox)
                    # These have large apparent size but minimal actual disk usage
                    if apparent_size > 1_000_000 and size < apparent_size * 0.01:
                        continue

                    # Also skip if actual disk usage is below threshold
                    if size < min_bytes:
                        continue

                    target = str(path).replace(str(Path.home()), "~")
                    if self._is_snoozed(target):
                        continue

                    staleness = self._get_staleness_days(path)

                    # Determine category based on path/extension
                    category = "file"
                    reason = "Large file, re-download if needed"

                    suffix = path.suffix.lower()
                    if suffix in [".iso", ".dmg", ".pkg"]:
                        reason = "Installer image, re-download if needed"
                    elif suffix in [".zip", ".tar", ".gz", ".7z", ".rar"]:
                        category = "archive"
                        reason = "Archive file"
                    elif suffix in [".gguf", ".bin", ".safetensors", ".pt", ".onnx"]:
                        category = "model"
                        reason = "LLM/ML model, re-download if needed"
                    elif suffix in [".mp4", ".mov", ".avi", ".mkv"]:
                        reason = "Video file"
                    elif "backup" in str(path).lower():
                        category = "backup"
                        reason = "Backup file"

                    # Check if it's a VM disk
                    if suffix in [".qcow2", ".vmdk", ".vdi", ".raw"]:
                        reason = "VM disk image - verify not in use"

                    options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                    # Add compress option for non-compressed files
                    if suffix not in [".zip", ".gz", ".7z", ".dmg", ".rar", ".xz"]:
                        options.append({
                            "id": "compress",
                            "reclaim_bytes": int(size * 0.7),  # Estimate 30% compression
                            "reversible": True
                        })

                    findings.append(self._make_finding(
                        target=target,
                        category=category,
                        size_bytes=size,
                        file_count=1,
                        staleness_days=staleness,
                        reason=reason,
                        options=options,
                        recommendation="delete" if staleness > 90 else "skip",
                    ))

                except (OSError, PermissionError):
                    continue

        self._save_phase_result("large_files", findings)
        return findings

    # === PHASE: Build Artifacts ===
    def scan_build_artifacts(self) -> list:
        """Find stale build artifacts and dependencies."""
        print("Scanning for build artifacts...")
        findings = []
        stale_days = self.config["thresholds"]["stale_days"]

        for scan_path in self.config["scan_paths"]:
            root = Path(scan_path).expanduser()
            if not root.exists():
                continue

            # Find project directories by marker files
            for marker, artifact_dirs, category, restore_hint in BUILD_ARTIFACTS:
                if "*" in marker:
                    # Glob pattern
                    marker_files = list(root.rglob(marker))
                else:
                    marker_files = list(root.rglob(marker))

                for marker_path in marker_files:
                    if self._is_excluded(marker_path):
                        continue

                    project_dir = marker_path.parent
                    project_staleness = self._get_staleness_days(marker_path)

                    for artifact_name in artifact_dirs:
                        artifact_path = project_dir / artifact_name
                        if not artifact_path.exists() or not artifact_path.is_dir():
                            continue

                        target = str(artifact_path).replace(str(Path.home()), "~")
                        if self._is_snoozed(target):
                            continue

                        size, file_count = self._get_dir_size(artifact_path)
                        if size < 10 * 1e6:  # Skip if less than 10MB
                            continue

                        staleness = self._get_staleness_days(artifact_path)

                        options = [
                            {"id": "delete", "reclaim_bytes": size, "reversible": False},
                        ]

                        # Add compress option
                        options.append({
                            "id": "compress",
                            "reclaim_bytes": int(size * 0.8),
                            "reversible": True
                        })

                        # Recommend delete if stale, otherwise skip
                        if staleness > stale_days:
                            recommendation = "delete"
                        else:
                            recommendation = "skip"

                        project_str = str(project_dir).replace(str(Path.home()), "~")

                        findings.append(self._make_finding(
                            target=target,
                            category=category,
                            size_bytes=size,
                            file_count=file_count,
                            staleness_days=staleness,
                            reason=restore_hint,
                            options=options,
                            recommendation=recommendation,
                            parent_project=project_str,
                        ))

        # Dedupe by target path
        seen = set()
        unique = []
        for f in findings:
            if f["target"] not in seen:
                seen.add(f["target"])
                unique.append(f)

        self._save_phase_result("build_artifacts", unique)
        return unique

    # === PHASE: Git Repos ===
    def scan_git_repos(self) -> list:
        """Find git repos that could benefit from gc."""
        print("Scanning git repositories...")
        findings = []

        for scan_path in self.config["scan_paths"]:
            root = Path(scan_path).expanduser()
            if not root.exists():
                continue

            for git_dir in root.rglob(".git"):
                if not git_dir.is_dir():
                    continue
                if self._is_excluded(git_dir):
                    continue

                repo_dir = git_dir.parent
                target = str(git_dir).replace(str(Path.home()), "~")

                if self._is_snoozed(target):
                    continue

                # Get .git directory size
                size, file_count = self._get_dir_size(git_dir)

                if size < 100 * 1e6:  # Skip if less than 100MB
                    continue

                staleness = self._get_staleness_days(git_dir)

                # Count loose objects
                loose_objects = 0
                objects_dir = git_dir / "objects"
                if objects_dir.exists():
                    try:
                        result = subprocess.run(
                            ["git", "-C", str(repo_dir), "count-objects", "-v"],
                            capture_output=True, text=True, timeout=10
                        )
                        for line in result.stdout.splitlines():
                            if line.startswith("count:"):
                                loose_objects = int(line.split(":")[1].strip())
                                break
                    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                        pass

                # Estimate gc savings (typically 30-50% for repos with many loose objects)
                gc_savings = int(size * 0.4) if loose_objects > 1000 else int(size * 0.2)

                options = [{
                    "id": "git-gc",
                    "reclaim_bytes": gc_savings,
                    "reversible": False
                }]

                # Recommend gc if many loose objects
                if loose_objects > 5000:
                    recommendation = "git-gc"
                    reason = f"{loose_objects:,} loose objects"
                elif loose_objects > 1000:
                    recommendation = "git-gc"
                    reason = f"{loose_objects:,} loose objects"
                else:
                    recommendation = "skip"
                    reason = "Git repository"

                repo_str = str(repo_dir).replace(str(Path.home()), "~")

                findings.append(self._make_finding(
                    target=target,
                    category="git",
                    size_bytes=size,
                    file_count=file_count,
                    staleness_days=staleness,
                    reason=reason,
                    options=options,
                    recommendation=recommendation,
                    parent_project=repo_str,
                    loose_objects=loose_objects,
                    gc_potential_bytes=gc_savings,
                ))

        self._save_phase_result("git_repos", findings)
        return findings

    # === PHASE: Applications ===
    def scan_applications(self) -> list:
        """Find large/stale applications."""
        print("Scanning applications...")
        findings = []
        stale_days = self.config["thresholds"]["stale_app_days"]

        app_dirs = [Path("/Applications"), Path.home() / "Applications"]

        for app_dir in app_dirs:
            if not app_dir.exists():
                continue

            for app_path in app_dir.glob("*.app"):
                if self._is_excluded(app_path):
                    continue

                target = str(app_path).replace(str(Path.home()), "~")
                if self._is_snoozed(target):
                    continue

                size, file_count = self._get_dir_size(app_path)

                if size < 500 * 1e6:  # Skip if less than 500MB
                    continue

                # Get last used date via mdls
                last_used = None
                staleness = 0
                try:
                    result = subprocess.run(
                        ["mdls", "-name", "kMDItemLastUsedDate", "-raw", str(app_path)],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.stdout and result.stdout != "(null)":
                        # Parse date like "2025-06-01 14:22:00 +0000"
                        date_str = result.stdout.strip()
                        try:
                            last_used_dt = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
                            staleness = (datetime.now() - last_used_dt).days
                            last_used = last_used_dt.isoformat()
                        except ValueError:
                            staleness = self._get_staleness_days(app_path)
                    else:
                        staleness = self._get_staleness_days(app_path)
                except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                    staleness = self._get_staleness_days(app_path)

                # Check if from App Store (has receipt)
                is_app_store = (app_path / "Contents" / "_MASReceipt").exists()

                options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                if staleness > stale_days:
                    recommendation = "delete"
                    reason = "App Store reinstall" if is_app_store else "Re-download from vendor"
                else:
                    recommendation = "skip"
                    reason = "Actively used"

                findings.append(self._make_finding(
                    target=target,
                    category="app",
                    size_bytes=size,
                    file_count=file_count,
                    staleness_days=staleness,
                    reason=reason,
                    options=options,
                    recommendation=recommendation,
                    app_store=is_app_store,
                ))

        self._save_phase_result("applications", findings)
        return findings

    # === PHASE: Library Leftovers ===
    def scan_library_leftovers(self) -> list:
        """Find orphaned application support directories."""
        print("Scanning library leftovers...")
        findings = []

        # Get list of installed app bundle IDs and names
        installed_apps = set()
        for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
            if app_dir.exists():
                # Search recursively for .app bundles (handles Adobe apps in subdirs)
                for app_path in app_dir.rglob("*.app"):
                    # Get bundle identifier
                    plist = app_path / "Contents" / "Info.plist"
                    if plist.exists():
                        try:
                            result = subprocess.run(
                                ["defaults", "read", str(plist), "CFBundleIdentifier"],
                                capture_output=True, text=True, timeout=5
                            )
                            if result.returncode == 0:
                                bundle_id = result.stdout.strip()
                                installed_apps.add(bundle_id)
                                # Also add parts of bundle ID (e.g., "adobe" from "com.adobe.Photoshop")
                                for part in bundle_id.split("."):
                                    if len(part) > 2:
                                        installed_apps.add(part.lower())
                        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                            pass
                    # Also add app name and parent folder name
                    installed_apps.add(app_path.stem.lower())
                    installed_apps.add(app_path.parent.name.lower())

        # Scan Application Support
        app_support = Path.home() / "Library" / "Application Support"
        if app_support.exists():
            for item in app_support.iterdir():
                if not item.is_dir():
                    continue
                if self._is_excluded(item):
                    continue

                target = str(item).replace(str(Path.home()), "~")
                if self._is_snoozed(target):
                    continue

                # Check if app is installed
                # Normalize by removing spaces, hyphens, underscores for comparison
                def normalize(s):
                    return s.lower().replace(" ", "").replace("-", "").replace("_", "")

                item_norm = normalize(item.name)
                is_orphan = True
                for app in installed_apps:
                    app_norm = normalize(app)
                    if item_norm in app_norm or app_norm in item_norm:
                        is_orphan = False
                        break

                if not is_orphan:
                    continue

                size, file_count = self._get_dir_size(item)
                if size < 50 * 1e6:  # Skip if less than 50MB
                    continue

                staleness = self._get_staleness_days(item)

                options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                findings.append(self._make_finding(
                    target=target,
                    category="orphan",
                    size_bytes=size,
                    file_count=file_count,
                    staleness_days=staleness,
                    reason=f"{item.name} not installed",
                    options=options,
                    recommendation="delete",
                    app_installed=False,
                ))

        self._save_phase_result("library_leftovers", findings)
        return findings

    # === PHASE: Caches ===
    def scan_caches(self) -> list:
        """Find large cache directories."""
        print("Scanning caches...")
        findings = []

        cache_root = Path.home() / "Library" / "Caches"
        if cache_root.exists():
            for item in cache_root.iterdir():
                if not item.is_dir():
                    continue
                if self._is_excluded(item):
                    continue

                target = str(item).replace(str(Path.home()), "~")
                if self._is_snoozed(target):
                    continue

                size, file_count = self._get_dir_size(item)
                if size < 100 * 1e6:  # Skip if less than 100MB
                    continue

                staleness = self._get_staleness_days(item)

                options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                # Caches are always safe to delete - they regenerate automatically
                findings.append(self._make_finding(
                    target=target,
                    category="cache",
                    size_bytes=size,
                    file_count=file_count,
                    staleness_days=staleness,
                    reason="Cache, auto-regenerates",
                    options=options,
                    recommendation="delete",
                ))

        # Also scan other cache locations
        for cache_path, category in CACHE_DIRS:
            path = Path(cache_path).expanduser()
            if not path.exists():
                continue

            target = str(path).replace(str(Path.home()), "~")
            if self._is_snoozed(target):
                continue

            size, file_count = self._get_dir_size(path)
            if size < 100 * 1e6:
                continue

            staleness = self._get_staleness_days(path)

            options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

            # Caches are always safe to delete
            findings.append(self._make_finding(
                target=target,
                category=category,
                size_bytes=size,
                file_count=file_count,
                staleness_days=staleness,
                reason="Cache, auto-regenerates",
                options=options,
                recommendation="delete",
            ))

        # Model directories
        for model_path, category in MODEL_DIRS:
            path = Path(model_path).expanduser()
            if not path.exists():
                continue

            # Scan subdirectories for individual models
            for item in path.iterdir():
                if not item.is_dir():
                    continue

                target = str(item).replace(str(Path.home()), "~")
                if self._is_snoozed(target):
                    continue

                size, file_count = self._get_dir_size(item)
                if size < 500 * 1e6:  # Models are typically large
                    continue

                staleness = self._get_staleness_days(item)

                options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                findings.append(self._make_finding(
                    target=target,
                    category="model",
                    size_bytes=size,
                    file_count=file_count,
                    staleness_days=staleness,
                    reason="ollama pull / huggingface download",
                    options=options,
                    recommendation="delete" if staleness > 30 else "skip",
                ))

        self._save_phase_result("caches", findings)
        return findings

    # === PHASE: Logs ===
    def scan_logs(self) -> list:
        """Find oversized log files."""
        print("Scanning log files...")
        findings = []
        min_bytes = self.config["thresholds"]["log_file_min_mb"] * 1e6

        log_patterns = ["*.log", "*.log.*", "*.out", "*.err"]
        log_dirs = [
            Path.home() / "Library" / "Logs",
            Path.home() / ".local" / "share",
        ]

        for log_dir in log_dirs:
            if not log_dir.exists():
                continue

            for pattern in log_patterns:
                for log_file in log_dir.rglob(pattern):
                    if not log_file.is_file():
                        continue
                    if self._is_excluded(log_file):
                        continue

                    try:
                        size = log_file.stat().st_size
                        if size < min_bytes:
                            continue

                        target = str(log_file).replace(str(Path.home()), "~")
                        if self._is_snoozed(target):
                            continue

                        staleness = self._get_staleness_days(log_file)

                        options = [{"id": "delete", "reclaim_bytes": size, "reversible": False}]

                        findings.append(self._make_finding(
                            target=target,
                            category="logs",
                            size_bytes=size,
                            file_count=1,
                            staleness_days=staleness,
                            reason="Log file, safe to delete",
                            options=options,
                            recommendation="delete",
                        ))
                    except OSError:
                        continue

        self._save_phase_result("logs", findings)
        return findings

    # === Merge & Output ===
    def merge_results(self) -> dict:
        """Merge all phase results into final scan.json."""
        all_findings = []

        phases = ["large_files", "build_artifacts", "git_repos",
                  "applications", "library_leftovers", "caches", "logs"]

        for phase in phases:
            results = self._load_phase_result(phase)
            if results:
                all_findings.extend(results)
                print(f"  Loaded {len(results)} findings from {phase}")

        # Dedupe by target and handle nested paths
        # Prefer more specific (child) paths - skip items whose children are already seen
        # First sort by path depth descending (children first), then by size descending
        all_findings.sort(key=lambda x: (-x["target"].count("/"), -x["size_bytes"]))

        seen_paths = set()
        unique = []
        for f in all_findings:
            target = f["target"]
            # Check if this is a parent of any already-seen path (skip if so)
            is_parent_of_seen = any(
                seen.startswith(target + "/") for seen in seen_paths
            )
            if target not in seen_paths and not is_parent_of_seen:
                seen_paths.add(target)
                unique.append(f)

        # Sort by size descending
        unique.sort(key=lambda x: x["size_bytes"], reverse=True)

        total_bytes = sum(f["size_bytes"] for f in unique)

        result = {
            "generated": datetime.now().isoformat(),
            "scan_duration_sec": 0,  # TODO: track actual duration
            "total_reclaimable_bytes": total_bytes,
            "item_count": len(unique),
            "findings": unique,
        }

        # Save to scan.json
        self.dcc_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.dcc_dir / "scan.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"\nSaved {len(unique)} findings ({self._format_size(total_bytes)}) to {output_path}")
        return result

    def run_phase(self, phase: str, force: bool = False) -> list:
        """Run a single phase, using cache if available."""
        if not force:
            cached = self._load_phase_result(phase)
            if cached is not None:
                print(f"Using cached results for {phase} ({len(cached)} findings)")
                return cached

        phase_methods = {
            "large_files": self.scan_large_files,
            "build_artifacts": self.scan_build_artifacts,
            "git_repos": self.scan_git_repos,
            "applications": self.scan_applications,
            "library_leftovers": self.scan_library_leftovers,
            "caches": self.scan_caches,
            "logs": self.scan_logs,
        }

        if phase in phase_methods:
            return phase_methods[phase]()
        else:
            print(f"Unknown phase: {phase}")
            return []

    def run_all(self, force: bool = False):
        """Run all enabled phases."""
        start = datetime.now()

        for phase, enabled in self.config["phases"].items():
            if enabled:
                self.run_phase(phase, force=force)

        result = self.merge_results()

        duration = (datetime.now() - start).total_seconds()
        result["scan_duration_sec"] = int(duration)

        # Re-save with duration
        output_path = self.dcc_dir / "scan.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"\nCompleted in {duration:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="DCC Scout - Scan for cleanup opportunities")
    parser.add_argument("--config", "-c", type=Path, help="Config file path")
    parser.add_argument("--phase", "-p", type=str, help="Run specific phase(s), comma-separated")
    parser.add_argument("--merge", "-m", action="store_true", help="Merge existing phase results")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-scan, ignore cache")
    parser.add_argument("--dcc-dir", type=Path, help="DCC directory (default: ~/.dcc)")

    args = parser.parse_args()

    scout = DccScout(
        config_path=args.config,
        dcc_dir=args.dcc_dir,
    )

    if args.merge:
        scout.merge_results()
    elif args.phase:
        phases = [p.strip() for p in args.phase.split(",")]
        for phase in phases:
            scout.run_phase(phase, force=args.force)
        scout.merge_results()
    else:
        scout.run_all(force=args.force)


if __name__ == "__main__":
    main()

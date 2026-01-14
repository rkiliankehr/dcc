#!/usr/bin/env python3
"""Tests for dcc-scout scanner."""

import json
import os
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from importlib.machinery import SourceFileLoader

# Load dcc-scout module
SCRIPT_DIR = Path(__file__).parent.parent
scout_module = SourceFileLoader('dcc_scout', str(SCRIPT_DIR / 'dcc-scout.py')).load_module()
DccScout = scout_module.DccScout


class TestFormatSize(unittest.TestCase):
    """Test _format_size utility method."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.scout = DccScout(dcc_dir=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_bytes(self):
        self.assertEqual(self.scout._format_size(500), "500.0 B")
        self.assertEqual(self.scout._format_size(0), "0.0 B")

    def test_kilobytes(self):
        self.assertEqual(self.scout._format_size(1024), "1.0 KB")
        self.assertEqual(self.scout._format_size(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(self.scout._format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(self.scout._format_size(500 * 1024 * 1024), "500.0 MB")

    def test_gigabytes(self):
        self.assertEqual(self.scout._format_size(1024 ** 3), "1.0 GB")
        self.assertEqual(self.scout._format_size(int(4.5 * 1024 ** 3)), "4.5 GB")

    def test_terabytes(self):
        self.assertEqual(self.scout._format_size(1024 ** 4), "1.0 TB")


class TestConfigLoading(unittest.TestCase):
    """Test config file loading."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_config_when_no_file(self):
        scout = DccScout(dcc_dir=self.tmp_dir)
        self.assertIn("scan_paths", scout.config)
        self.assertIn("thresholds", scout.config)
        self.assertIn("phases", scout.config)

    def test_loads_custom_config(self):
        config_path = Path(self.tmp_dir) / "config.yaml"
        config_path.write_text("""
scan_paths:
  - ~/custom
thresholds:
  large_file_min_gb: 5
""")
        scout = DccScout(dcc_dir=self.tmp_dir, config_path=config_path)
        self.assertIn("~/custom", scout.config["scan_paths"])
        self.assertEqual(scout.config["thresholds"]["large_file_min_gb"], 5)
        # Default values should be preserved
        self.assertIn("stale_days", scout.config["thresholds"])

    def test_handles_empty_config(self):
        config_path = Path(self.tmp_dir) / "config.yaml"
        config_path.write_text("")
        scout = DccScout(dcc_dir=self.tmp_dir, config_path=config_path)
        # Should use defaults
        self.assertEqual(scout.config["thresholds"]["large_file_min_gb"], 1)


class TestSnoozedLoading(unittest.TestCase):
    """Test snoozed.json loading."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_empty_when_no_file(self):
        scout = DccScout(dcc_dir=self.tmp_dir)
        self.assertEqual(len(scout.snoozed), 0)

    def test_loads_active_snoozes(self):
        snoozed_path = Path(self.tmp_dir) / "snoozed.json"
        future = (datetime.now() + timedelta(days=7)).isoformat()
        snoozed_path.write_text(json.dumps({
            "version": 1,
            "items": [
                {"target": "~/test/path", "expires_at": future}
            ]
        }))
        scout = DccScout(dcc_dir=self.tmp_dir)
        self.assertIn("~/test/path", scout.snoozed)

    def test_ignores_expired_snoozes(self):
        snoozed_path = Path(self.tmp_dir) / "snoozed.json"
        past = (datetime.now() - timedelta(days=1)).isoformat()
        snoozed_path.write_text(json.dumps({
            "version": 1,
            "items": [
                {"target": "~/expired/path", "expires_at": past}
            ]
        }))
        scout = DccScout(dcc_dir=self.tmp_dir)
        self.assertNotIn("~/expired/path", scout.snoozed)

    def test_handles_corrupt_file(self):
        snoozed_path = Path(self.tmp_dir) / "snoozed.json"
        snoozed_path.write_text("not valid json{")
        scout = DccScout(dcc_dir=self.tmp_dir)
        self.assertEqual(len(scout.snoozed), 0)


class TestExclusionLogic(unittest.TestCase):
    """Test path exclusion logic."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_excludes_configured_paths(self):
        scout = DccScout(dcc_dir=self.tmp_dir)
        excluded = Path(self.tmp_dir) / "excluded"
        scout.config["exclude_paths"] = [str(excluded)]

        excluded_path = excluded / "subdir"
        excluded_path.mkdir(parents=True)

        self.assertTrue(scout._is_excluded(excluded_path))
        self.assertFalse(scout._is_excluded(Path(self.tmp_dir) / "other"))


class TestMakeFinding(unittest.TestCase):
    """Test finding creation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_valid_finding(self):
        scout = DccScout(dcc_dir=self.tmp_dir)
        test_file = Path(self.tmp_dir) / "test.txt"
        test_file.write_text("test content")

        finding = scout._make_finding(
            target=str(test_file),
            category="file",
            size_bytes=12,
            file_count=1,
            staleness_days=5,
            reason="test reason",
            options=[{"id": "delete", "reclaim_bytes": 12, "reversible": False}],
            recommendation="delete",
        )

        self.assertEqual(finding["category"], "file")
        self.assertEqual(finding["size_bytes"], 12)
        self.assertEqual(finding["size_human"], "12.0 B")
        self.assertEqual(finding["file_count"], 1)
        self.assertEqual(finding["staleness_days"], 5)
        self.assertEqual(finding["reason"], "test reason")
        self.assertEqual(finding["recommendation"], "delete")
        self.assertFalse(finding["is_archive"])
        self.assertEqual(len(finding["options"]), 1)


class TestBuildArtifactsScan(unittest.TestCase):
    """Test build artifacts scanning."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_finds_node_modules(self):
        # Create a mock Node.js project
        project = Path(self.tmp_dir) / "myproject"
        project.mkdir()
        (project / "package.json").write_text('{"name": "test"}')

        node_modules = project / "node_modules"
        node_modules.mkdir()
        # Create enough content to exceed 10MB threshold
        for i in range(100):
            subdir = node_modules / f"pkg{i}"
            subdir.mkdir()
            (subdir / "index.js").write_text("x" * 100000)  # 100KB each = 10MB total

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.scan_build_artifacts()

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "node")
        self.assertIn("node_modules", findings[0]["target"])
        self.assertEqual(findings[0]["reason"], "npm install")

    def test_finds_rust_target(self):
        # Create a mock Rust project
        project = Path(self.tmp_dir) / "rustproject"
        project.mkdir()
        (project / "Cargo.toml").write_text('[package]\nname = "test"')

        target = project / "target"
        target.mkdir()
        # Create enough content
        for i in range(100):
            (target / f"file{i}.rlib").write_text("x" * 100000)

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.scan_build_artifacts()

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "rust")
        self.assertIn("target", findings[0]["target"])
        self.assertEqual(findings[0]["reason"], "cargo build")

    def test_finds_python_venv(self):
        # Create a mock Python project
        project = Path(self.tmp_dir) / "pyproject"
        project.mkdir()
        (project / "requirements.txt").write_text("pytest")

        venv = project / "venv"
        venv.mkdir()
        # Create enough content
        for i in range(100):
            (venv / f"pkg{i}.py").write_text("x" * 100000)

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.scan_build_artifacts()

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "venv")

    def test_skips_small_artifacts(self):
        # Create a small node_modules (< 10MB)
        project = Path(self.tmp_dir) / "small"
        project.mkdir()
        (project / "package.json").write_text('{}')

        node_modules = project / "node_modules"
        node_modules.mkdir()
        (node_modules / "tiny.js").write_text("small")

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.scan_build_artifacts()

        self.assertEqual(len(findings), 0)


class TestLargeFilesScan(unittest.TestCase):
    """Test large files scanning."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_finds_large_files(self):
        # Create a large file (fake via threshold override)
        large_file = Path(self.tmp_dir) / "large.bin"
        large_file.write_text("x" * 1000)

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]
        scout.config["thresholds"]["large_file_min_gb"] = 0.000001  # ~1KB

        findings = scout.scan_large_files()

        self.assertGreaterEqual(len(findings), 1)
        found_targets = [f["target"] for f in findings]
        self.assertTrue(any("large.bin" in t for t in found_targets))

    def test_categorizes_iso_files(self):
        iso_file = Path(self.tmp_dir) / "ubuntu.iso"
        iso_file.write_text("x" * 2000)

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]
        scout.config["thresholds"]["large_file_min_gb"] = 0.000001

        findings = scout.scan_large_files()

        iso_findings = [f for f in findings if "ubuntu.iso" in f["target"]]
        self.assertEqual(len(iso_findings), 1)
        self.assertIn("re-download", iso_findings[0]["reason"].lower())

    def test_categorizes_model_files(self):
        model_file = Path(self.tmp_dir) / "model.gguf"
        model_file.write_text("x" * 2000)

        dcc_dir = Path(self.tmp_dir) / "dcc"
        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]
        scout.config["thresholds"]["large_file_min_gb"] = 0.000001

        findings = scout.scan_large_files()

        model_findings = [f for f in findings if "model.gguf" in f["target"]]
        self.assertEqual(len(model_findings), 1)
        self.assertEqual(model_findings[0]["category"], "model")


class TestMergeResults(unittest.TestCase):
    """Test result merging."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_merges_phase_results(self):
        dcc_dir = Path(self.tmp_dir) / "dcc"
        state_dir = dcc_dir / "state"
        state_dir.mkdir(parents=True)

        # Create phase results
        (state_dir / "large_files.json").write_text(json.dumps({
            "phase": "large_files",
            "findings": [
                {"target": "~/file1.bin", "size_bytes": 1000, "category": "file"}
            ]
        }))
        (state_dir / "build_artifacts.json").write_text(json.dumps({
            "phase": "build_artifacts",
            "findings": [
                {"target": "~/proj/node_modules", "size_bytes": 2000, "category": "node"}
            ]
        }))

        scout = DccScout(dcc_dir=dcc_dir)
        result = scout.merge_results()

        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["total_reclaimable_bytes"], 3000)
        self.assertEqual(len(result["findings"]), 2)

    def test_deduplicates_findings(self):
        dcc_dir = Path(self.tmp_dir) / "dcc"
        state_dir = dcc_dir / "state"
        state_dir.mkdir(parents=True)

        # Same target in two phases
        (state_dir / "large_files.json").write_text(json.dumps({
            "phase": "large_files",
            "findings": [
                {"target": "~/duplicate.bin", "size_bytes": 1000, "category": "file"}
            ]
        }))
        (state_dir / "build_artifacts.json").write_text(json.dumps({
            "phase": "build_artifacts",
            "findings": [
                {"target": "~/duplicate.bin", "size_bytes": 1000, "category": "file"}
            ]
        }))

        scout = DccScout(dcc_dir=dcc_dir)
        result = scout.merge_results()

        self.assertEqual(result["item_count"], 1)

    def test_sorts_by_size_descending(self):
        dcc_dir = Path(self.tmp_dir) / "dcc"
        state_dir = dcc_dir / "state"
        state_dir.mkdir(parents=True)

        (state_dir / "large_files.json").write_text(json.dumps({
            "phase": "large_files",
            "findings": [
                {"target": "~/small.bin", "size_bytes": 100, "category": "file"},
                {"target": "~/large.bin", "size_bytes": 10000, "category": "file"},
                {"target": "~/medium.bin", "size_bytes": 1000, "category": "file"},
            ]
        }))

        scout = DccScout(dcc_dir=dcc_dir)
        result = scout.merge_results()

        sizes = [f["size_bytes"] for f in result["findings"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class TestPhaseRunning(unittest.TestCase):
    """Test phase execution."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_uses_cache_when_available(self):
        dcc_dir = Path(self.tmp_dir) / "dcc"
        state_dir = dcc_dir / "state"
        state_dir.mkdir(parents=True)

        cached_data = {
            "phase": "build_artifacts",
            "findings": [{"target": "~/cached", "size_bytes": 999, "category": "node"}]
        }
        (state_dir / "build_artifacts.json").write_text(json.dumps(cached_data))

        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.run_phase("build_artifacts", force=False)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["target"], "~/cached")

    def test_force_rescans(self):
        dcc_dir = Path(self.tmp_dir) / "dcc"
        state_dir = dcc_dir / "state"
        state_dir.mkdir(parents=True)

        # Put stale cached data
        (state_dir / "build_artifacts.json").write_text(json.dumps({
            "phase": "build_artifacts",
            "findings": [{"target": "~/stale", "size_bytes": 1, "category": "node"}]
        }))

        scout = DccScout(dcc_dir=dcc_dir)
        scout.config["scan_paths"] = [self.tmp_dir]

        findings = scout.run_phase("build_artifacts", force=True)

        # Should have rescanned and found nothing in empty tmp_path
        self.assertFalse(any(f["target"] == "~/stale" for f in findings))


class TestDirSize(unittest.TestCase):
    """Test directory size calculation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_calculates_total_size(self):
        test_dir = Path(self.tmp_dir) / "test"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("a" * 100)
        (test_dir / "file2.txt").write_text("b" * 200)

        subdir = test_dir / "sub"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("c" * 300)

        scout = DccScout(dcc_dir=Path(self.tmp_dir) / "dcc")
        size, count = scout._get_dir_size(test_dir)

        # Size is actual disk blocks, not apparent size
        # Small files typically use at least one block (usually 4KB)
        # So 3 files use at least 3 blocks worth of space
        self.assertGreater(size, 0)
        self.assertEqual(count, 3)

    def test_handles_empty_dir(self):
        scout = DccScout(dcc_dir=self.tmp_dir)
        empty_dir = Path(self.tmp_dir) / "empty"
        empty_dir.mkdir()
        size, count = scout._get_dir_size(empty_dir)
        self.assertEqual(size, 0)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

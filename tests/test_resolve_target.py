"""Exercise the Python check resolver with runner fixtures and mocked networking."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from resolve_target import NoRedirect, github_commit_sha, resolve_target, write_outputs

SHA = "a" * 40
BASE = "b" * 40
SCRIPT = Path(__file__).parents[1] / "resolve_target.py"


class ResolveTargetTests(unittest.TestCase):
    """Preserve event behavior and fail closed before publishing invalid targets."""

    def setUp(self) -> None:
        """Create an isolated workspace and coherent event/API metadata."""
        temporary = tempfile.TemporaryDirectory(prefix="check-target-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = {
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_REPOSITORY": "Example/target",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_SHA": SHA,
            "GITHUB_REF": "refs/heads/main",
            "GET_PR_REF": "feature",
            "GET_PR_REPO": "Fork/target",
            "GET_PR_SHA": SHA,
            "GET_PR_BASE_SHA": BASE,
        }
        self.payload = {
            "pull_request": {"number": 1, "base": {"sha": BASE}},
            "issue": {"number": 1, "pull_request": {}},
            "inputs": {"ref": "feature"},
        }
        self.lookup = Mock(return_value=SHA)

    def test_pr_schedule_and_push_preserve_ref_and_sha(self) -> None:
        """Already immutable event targets require no later branch lookup."""
        for event in ["pull_request", "schedule", "push"]:
            with self.subTest(event=event):
                outputs = resolve_target(
                    self.inputs | {"GITHUB_EVENT_NAME": event}, self.payload, self.lookup
                )
                self.assertEqual(outputs["ref"], SHA)
                self.assertEqual(outputs["sha"], SHA)
        self.lookup.assert_not_called()

    def test_manual_branch_is_resolved_once(self) -> None:
        """Keep the branch output separate from its resolved source SHA."""
        outputs = resolve_target(
            self.inputs | {"GITHUB_EVENT_NAME": "workflow_dispatch"}, self.payload, self.lookup
        )
        self.assertEqual(outputs["ref"], "feature")
        self.assertEqual(outputs["sha"], SHA)
        self.lookup.assert_called_once_with("Example/target", "feature")

    def test_comment_approval_and_fork_identity(self) -> None:
        """Missing approval and moved or mismatched PR snapshots cannot succeed."""
        inputs = self.inputs | {"GITHUB_EVENT_NAME": "issue_comment"}
        for overrides in [
            {},
            {"INPUT_SHA": "c" * 40},
            {"INPUT_SHA": SHA, "INPUT_REPO": "Other/target"},
            {"INPUT_SHA": SHA, "INPUT_REF": "other"},
            {"INPUT_SHA": SHA, "GET_PR_SHA": ""},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                resolve_target(inputs | overrides, self.payload, self.lookup)
        outputs = resolve_target(inputs | {"INPUT_SHA": SHA}, self.payload, self.lookup)
        self.assertEqual(
            (outputs["repo"], outputs["ref"], outputs["sha"]), ("Fork/target", "feature", SHA)
        )
        self.lookup.assert_not_called()

    def test_invalid_overrides_are_rejected(self) -> None:
        """Reject injection, invalid SHAs, traversal, and overlapping directories."""
        for overrides in [
            {"INPUT_REF": "x\nrepo=spoof"},
            {"INPUT_REPO": "Example/../target"},
            {"INPUT_PR_BASE_SHA": "x\rspoof=true"},
            {"INPUT_CHECKOUT_PATH": "../outside"},
            {"INPUT_BUILD_PATH": "/tmp/outside"},
            {"INPUT_CHECKOUT_PATH": "target-build"},
            {"INPUT_SHA": "c" * 40},
            {"INPUT_SHA": "0" * 40},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                resolve_target(self.inputs | overrides, self.payload, self.lookup)

    def test_symlink_escape_is_rejected(self) -> None:
        """Existing ancestors cannot redirect a future checkout outside the workspace."""
        (self.root / "escape").symlink_to(self.root.parent)
        with self.assertRaises(ValueError):
            resolve_target(
                self.inputs | {"INPUT_CHECKOUT_PATH": "escape/source"}, self.payload, self.lookup
            )

    def test_symlink_aliases_cannot_make_checkout_and_build_overlap(self) -> None:
        """Compare resolved containment while preserving workspace-relative outputs."""
        source = self.root / "source"
        source.mkdir()
        (self.root / "alias").symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            resolve_target(
                self.inputs | {"INPUT_CHECKOUT_PATH": "source", "INPUT_BUILD_PATH": "alias"},
                self.payload,
                self.lookup,
            )

        outputs = resolve_target(
            self.inputs
            | {"INPUT_CHECKOUT_PATH": "source-checkout", "INPUT_BUILD_PATH": "source-build"},
            self.payload,
            self.lookup,
        )
        self.assertEqual(outputs["checkout_path"], "source-checkout")
        self.assertEqual(outputs["build_path"], "source-build")

    def test_cross_repository_snapshot_must_match_ref(self) -> None:
        """Explicit repository/ref/SHA overrides remain one validated tuple."""
        inputs = self.inputs | {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "INPUT_REPO": "Fork/target",
            "INPUT_REF": "feature",
            "INPUT_SHA": "c" * 40,
        }
        with self.assertRaises(ValueError):
            resolve_target(inputs, self.payload, self.lookup)
        self.lookup.assert_called_once_with("Fork/target", "feature")

    def test_invalid_output_set_is_not_partially_written(self) -> None:
        """Validate every value before opening the runner output file."""
        output = self.root / "outputs"
        output.write_text("existing=value\n")
        with self.assertRaises(ValueError):
            write_outputs(output, {"ref": SHA, "repo": "Example/target\nspoof=true"})
        self.assertEqual(output.read_text(), "existing=value\n")

    def test_cli_ignores_target_python_configuration(self) -> None:
        """The shipped isolated invocation ignores source-controlled sitecustomize code."""
        sentinel = self.root / "executed"
        (self.root / "sitecustomize.py").write_text(f"open({str(sentinel)!r}, 'w').close()\n")
        event = self.root / "event.json"
        event.write_text(json.dumps(self.payload))
        output = self.root / "outputs"
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            **self.inputs,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_OUTPUT": str(output),
            "PYTHONPATH": str(self.root),
            "GITHUB_TOKEN": "TEST_SENTINEL",
        }
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertIn(f"sha={SHA}\n", output.read_text())
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            cwd=self.root,
            env=environment | {"INPUT_REF": "x\nspoof=true"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("spoof=true", result.stderr)


class MetadataTransportTests(unittest.TestCase):
    """Check GET-only, encoded, bounded, and non-redirecting API requests."""

    def test_request_is_encoded_and_authenticated(self) -> None:
        """Use a mock response instead of contacting GitHub with credentials."""
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"sha": SHA}).encode()
        with patch("resolve_target.build_opener") as opener:
            opener.return_value.open.return_value = response
            value = github_commit_sha(
                "Example/target", "feature/test#name", {"GITHUB_TOKEN": "TEST_SENTINEL"}
            )
        self.assertEqual(value, SHA)
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("feature%2Ftest%23name?per_page=1", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer TEST_SENTINEL")
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 30)
        self.assertIsInstance(opener.call_args.args[0], NoRedirect)
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")
        )

    def test_denial_or_invalid_response_does_not_fall_back(self) -> None:
        """API denial and malformed metadata remain visible failures."""
        with patch("resolve_target.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(
                "https://api.github.com", 403, "denied", {}, None
            )
            with self.assertRaisesRegex(ValueError, "metadata lookup failed"):
                github_commit_sha("Example/target", "feature", {"GITHUB_TOKEN": "TEST_SENTINEL"})
        for environment in [
            {},
            {"GITHUB_TOKEN": "TEST_SENTINEL", "GITHUB_API_URL": "http://elsewhere.invalid"},
        ]:
            with (
                self.subTest(environment=environment),
                patch("resolve_target.build_opener") as opener,
            ):
                with self.assertRaises(ValueError):
                    github_commit_sha("Example/target", "feature", environment)
                opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()

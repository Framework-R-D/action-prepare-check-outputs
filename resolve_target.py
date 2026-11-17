"""Resolve one check snapshot without treating metadata lookup as approval."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CommitLookup = Callable[[str, str], str]


class NoRedirect(HTTPRedirectHandler):
    """Keep an authenticated metadata request on its configured API endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects rather than forwarding an authorization header."""
        return None


def github_commit_sha(repository: str, ref: str, environment: Mapping[str, str]) -> str:
    """Fetch only the commit metadata needed for an immutable snapshot."""
    api = environment.get("GITHUB_API_URL", "https://api.github.com")
    parsed = urlsplit(api)
    token = environment.get("GITHUB_TOKEN", "")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not token
        or "\r" in token
        or "\n" in token
    ):
        raise ValueError("A trusted HTTPS GitHub API endpoint and token are required")
    request = Request(
        f"{api.rstrip('/')}/repos/{repository}/commits/{quote(ref, safe='')}?per_page=1",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        method="GET",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("Metadata response is too large")
        return validate_sha(json.loads(body)["sha"])
    except (URLError, OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("GitHub commit metadata lookup failed") from error


def validate_sha(value: str) -> str:
    """Require a complete, nonzero immutable SHA."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value) or value == "0" * 40:
        raise ValueError("Expected a full immutable source SHA")
    return value


def validate_repository(value: str) -> str:
    """Reject repository values that could alter an API route or output."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value or ""):
        raise ValueError("Invalid repository")
    if value.split("/")[1] in {".", ".."}:
        raise ValueError("Invalid repository")
    return value


def validate_ref(value: str) -> str:
    """Reject ref expressions, options, and output-injection characters."""
    if (
        not value
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or ".." in value
        or "@{" in value
        or value.startswith("-")
        or value.endswith(".")
        or any(
            not part or part.startswith(".") or part.endswith(".lock") for part in value.split("/")
        )
    ):
        raise ValueError("Invalid ref")
    return value


def relative_path(value: str, workspace: str) -> str:
    """Validate a relative path and every existing ancestor's containment."""
    if (
        not value
        or Path(value).is_absolute()
        or re.search(r"[\x00-\x1f\x7f\\]", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Expected a workspace-relative path without traversal")
    root = Path(workspace).resolve(strict=True)
    current = root
    for part in value.split("/"):
        current /= part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        current = current.resolve(strict=True)
        if current == root or not current.is_relative_to(root):
            raise ValueError("Path escapes workspace")
    return value


def resolve_target(
    inputs: Mapping[str, str], payload: Mapping[str, Any], lookup: CommitLookup
) -> dict[str, str]:
    """Preserve the legacy ref and publish one coherent immutable check target."""
    original_repo = validate_repository(inputs["GITHUB_REPOSITORY"])
    repository = inputs.get("INPUT_REPO", "") or original_repo
    ref = inputs.get("INPUT_REF", "")
    sha = inputs.get("INPUT_SHA", "")
    base = inputs.get("INPUT_PR_BASE_SHA", "")
    number = ""
    event = inputs["GITHUB_EVENT_NAME"]
    if event == "issue_comment":
        # A supplied SHA is caller approval; resolving the current head cannot grant it.
        validate_sha(sha)
        issue = payload["issue"]
        if not isinstance(issue.get("pull_request"), Mapping):
            raise ValueError("Comment is not on a PR")
        if inputs.get("INPUT_REPO") and repository != inputs.get("GET_PR_REPO"):
            raise ValueError("PR repository override mismatch")
        if ref and ref != inputs.get("GET_PR_REF"):
            raise ValueError("PR ref override mismatch")
        if sha != inputs.get("GET_PR_SHA"):
            raise ValueError("Approved PR snapshot has moved")
        repository = inputs.get("GET_PR_REPO", "")
        ref = inputs.get("GET_PR_REF", "")
        base = base or inputs.get("GET_PR_BASE_SHA", "")
        number = str(issue["number"])
    elif event == "pull_request":
        ref = ref or inputs["GITHUB_SHA"]
        base = base or payload["pull_request"]["base"]["sha"]
        number = str(payload["pull_request"]["number"])
        if not inputs.get("INPUT_REF") and not inputs.get("INPUT_REPO"):
            sha = sha or inputs["GITHUB_SHA"]
    elif event == "workflow_dispatch":
        ref = ref or payload.get("inputs", {}).get("ref") or inputs["GITHUB_REF"]
    else:
        ref = ref or inputs["GITHUB_SHA"]
        if not inputs.get("INPUT_REF") and not inputs.get("INPUT_REPO"):
            sha = sha or inputs["GITHUB_SHA"]
        base = base or payload.get("before") or ""
    validate_repository(repository)
    validate_ref(ref)
    if sha:
        validate_sha(sha)
    if sha and sha != ref and re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Source SHA does not match the immutable ref")
    if (
        sha
        and event != "issue_comment"
        and ref != sha
        and (inputs.get("INPUT_REF") or inputs.get("INPUT_REPO") or event == "workflow_dispatch")
    ):
        if lookup(repository, ref) != sha:
            raise ValueError("Explicit snapshot does not match target ref")
    if not sha:
        if repository != original_repo:
            raise ValueError("Cross-repository targets require an explicit source SHA")
        sha = lookup(repository, ref)
    validate_sha(sha)
    if base and not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Invalid base SHA")
    if number and not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Invalid PR number")
    name = repository.split("/")[1]
    checkout = relative_path(
        inputs.get("INPUT_CHECKOUT_PATH") or f"{name}-src", inputs["GITHUB_WORKSPACE"]
    )
    build = relative_path(
        inputs.get("INPUT_BUILD_PATH") or f"{name}-build", inputs["GITHUB_WORKSPACE"]
    )
    if checkout == build or checkout.startswith(build + "/") or build.startswith(checkout + "/"):
        raise ValueError("Checkout and build paths must not overlap")
    return {
        "ref": ref,
        "sha": sha,
        "repo": repository,
        "base_sha": base,
        "pr_number": number,
        "checkout_path": checkout,
        "build_path": build,
    }


def write_outputs(output_path: Path, outputs: Mapping[str, str]) -> None:
    """Validate the complete output set before appending any runner commands."""
    for name, value in outputs.items():
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]*", name)
            or not isinstance(value, str)
            or re.search(r"[\x00-\x1f\x7f]", value)
        ):
            raise ValueError("Invalid action output")
    with output_path.open("a", encoding="utf-8") as output:
        output.write("".join(f"{name}={value}\n" for name, value in outputs.items()))


def main() -> None:
    """Read runner data, resolve metadata, and publish only validated outputs."""
    environment = dict(os.environ)
    payload = json.loads(Path(environment["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    outputs = resolve_target(
        environment, payload, lambda repo, ref: github_commit_sha(repo, ref, environment)
    )
    write_outputs(Path(environment["GITHUB_OUTPUT"]), outputs)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit(
            "Check target resolution failed closed; verify snapshot inputs and metadata"
        ) from None

# `action-prepare-check-outputs`

> Determines environment, target refs, and build paths for check workflows across `pull_request`, `issue_comment`, and `workflow_dispatch` triggers.

## Usage

```yaml
- uses: Framework-R-D/action-prepare-check-outputs@cf162b18d8cb75101aac5c0837cc804abeaf0a13 # v1
  with:
    input-name: value
```

## Inputs

| Name | Description | Required | Default |
|------|-------------|----------|---------|
| ref | Manual ref override | false | |
| repo | Manual repo override | false | |
| sha | Full immutable source SHA; caller-approved SHA required for comments and cross-repository targets | false | |
| pr-base-sha | Manual base SHA override | false | |
| checkout-path | Manual checkout path override | false | |
| build-path | Manual build path override | false | |

## Outputs

| Name | Description |
|------|-------------|
| is_act | Whether running in act |
| ref | The resolved ref |
| sha | Immutable source SHA; resolution alone does not authorize execution |
| repo | The resolved repository |
| base_sha | The resolved base SHA for diffing |
| pr_number | The PR number if available |
| checkout_path | The resolved checkout path |
| build_path | The resolved build path |

## Snapshot contract

`ref` retains its existing event-dependent meaning; it is not silently replaced by `sha`. Consumers must check out and test `sha`, while keeping `ref` separate for branch operations. A manual mutable ref is resolved once; explicit ref/repository/SHA overrides must agree. Comment commands must supply a full SHA authorized by the caller for that exact PR repository. Missing approval or a PR head that moved before resolution fails closed. The metadata resolver does not authenticate reviewers or use commit timestamps as freshness evidence. Legacy comment commands need a caller-side approval change before adopting this release.

Repository, ref, SHA, PR number, and output paths are validated before any outputs are written. Checkout/build paths must be workspace-relative, non-overlapping, and free of traversal or existing symlink escapes. Callers must preserve that containment through subsequent checkout and file operations. Reusable callers must carry the same `repo`, `ref`, and `sha` across jobs rather than independently looking up a branch later.

Resolution and its tests use Python 3.12 or newer and the standard library; no local Node or npm dependency is needed. The isolated Python invocation ignores target-controlled Python configuration. Mutable-ref lookups use GET requests to the runner's GitHub API endpoint, reject redirects, and fail closed on API errors rather than selecting a different snapshot.

## License

[Apache 2.0](LICENSE)

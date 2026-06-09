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
| pr-base-sha | Manual base SHA override | false | |
| checkout-path | Manual checkout path override | false | |
| build-path | Manual build path override | false | |

## Outputs

| Name | Description |
|------|-------------|
| is_act | Whether running in act |
| ref | The resolved ref |
| repo | The resolved repository |
| base_sha | The resolved base SHA for diffing |
| pr_number | The PR number if available |
| checkout_path | The resolved checkout path |
| build_path | The resolved build path |

## License

[Apache 2.0](LICENSE)

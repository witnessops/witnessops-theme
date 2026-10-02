# Contributing

Keep each pull request focused on one component or one clear repository change. Include the reason for the change, what a reviewer can inspect, and the validation actually performed.

## Adding a component

1. Put source artwork in `assets/` and distributable files in `packages/`.
2. Record the source, license and attribution for any reused artwork. Retain its license text in `licenses/`.
3. Add build or preview helpers in `tools/` when the component needs them, and meaningful checks in `tests/`.
4. Document installation, removal or rollback, supported environments and known limitations with the component.
5. Store design decisions and dated validation evidence in `docs/`.

Placeholder folders do not establish compatibility or completed functionality. State which environment was checked and distinguish a package build or preview from an installed desktop test.

## Reviewing visual changes

Include a preview of the actual package files at the sizes used on the desktop. Explain any aliases or fallbacks that affect which icon or component is selected.

Keep host inventories, receipts, backups, credentials and temporary previews under ignored local paths. Share only the sanitized evidence needed to review the change.

## Current bootstrap

This change contains documentation and empty component placeholders. It has no build, test or installation command. Component-specific commands will be added with their implementation.

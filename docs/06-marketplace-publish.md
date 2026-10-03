# Publish to the VS Code Marketplace

This guide is for maintainers preparing a public Marketplace release. Publishing changes an external listing; finish the package and review it before uploading. For package construction, see [local deployment](05-local-deploy.md). This checkout does **not** include an automated release workflow.

## One-time preparation

1. Create a publisher through the [Marketplace management page](https://marketplace.visualstudio.com/manage).
2. Replace `your-publisher-id` in `package.json` with that publisher's ID.
3. Confirm that the repository, bugs, and homepage URLs in `package.json` still match the Git remote.
4. Check the license, icon, README, and changelog. Replace the placeholder extension ID in the example Dev Container configuration if you want users to install from its Marketplace ID.

The published extension ID is `<publisher>.python-memory-guardian`. This checkout uses the Git remote for its repository URLs, but the manifest's publisher ID is still a placeholder and must match a publisher you control before a real release.

## Prepare a release

1. Confirm the version in `package.json` and `package-lock.json` is higher than the published version, and finalize its `CHANGELOG.md` section. This checkout is prepared as 1.4.0; verify that number against the Marketplace listing before publishing.
2. Build and run the project tests:

   ```bash
   npm ci
   npm run build:rust
   npm test
   ```

   Rust is needed to verify both static-analysis backends. If you only distribute the Python backend, the Rust build is optional, but `npm run test:parity` will otherwise skip the Rust comparison.

3. Build the universal VSIX:

   ```bash
   npx vsce package
   ```

4. Check the package contents with `npx vsce ls --tree`. Run `python test-fixtures/package_test.py path/to/package.vsix` with the oldest and current supported Python, then install the VSIX in a clean VS Code environment and test warnings and profiling. Use the [local deployment checklist](05-local-deploy.md#install-and-check-it).

Build platform-specific VSIXs separately if you are distributing the Rust backend. Each package must contain a binary for its target OS and CPU. The [VS Code publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#platform-specific-extensions) explains `--target` packages and the universal fallback.

## 1.4.0 preparation status (October 3, 2026)

| Gate | Status in this checkout |
|---|---|
| Version and notes | `package.json` and `package-lock.json` are both 1.4.0; `CHANGELOG.md` has a 1.4.0 section. Confirm that 1.4.0 is newer than the live Marketplace version before upload. |
| Publisher and legal identity | Repository URLs match the configured Git remote. The publisher ID in `package.json` and the copyright holder in `LICENSE` are still placeholders; confirm their exact values with the owner. |
| Source and runtime tests | `npm test` passes on Python 3.13.0. Python/Rust parity and runtime tests pass on Python 3.9.6; two Python 3.12+ monitoring cases are skipped there. |
| Universal package | A pre-version-bump VSIX passed the packaged Python-server smoke test on Python 3.9.6 and 3.13.0. Build and inspect a final 1.4.0 VSIX after the publisher value is set. |
| Rust package | A fresh macOS ARM release binary builds and passes parity. A matching platform VSIX has not been produced or smoke-tested for 1.4.0. Other target platforms require their own builds. |
| Clean VS Code install | Pending a final 1.4.0 VSIX; verify static diagnostics and profiling in a clean VS Code profile before upload. |
| Source control | `AGENTS.md`, `MEMORY.md`, the documentation skill, and this guide are new files in the working tree. Include them with the 1.4.0 changes in the release commit. |

## Upload manually

Sign in to the [Marketplace management page](https://marketplace.visualstudio.com/manage), select the publisher, choose **New extension → Visual Studio Code** for a first release or **Update** for an existing extension, and upload the reviewed VSIX. Wait for Marketplace processing, then verify the listing and install it in a clean VS Code profile.

The [official publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) documents the current upload flow and listing constraints. Marketplace README and changelog images must resolve over HTTPS; the icon must not be SVG.

## Automated publishing

This repository has no `.github/workflows/release.yml`, so publishing is manual until a workflow is created and tested. Microsoft's publishing guidance recommends Microsoft Entra ID for secure automation. The [`vsce` project](https://github.com/microsoft/vscode-vsce#trusted-publishing) also documents OIDC trusted publishing for a configured GitHub Actions workflow; confirm that your publisher exposes the required trust-policy setup before using it.

Avoid starting new automation around a global Azure DevOps personal access token: [Microsoft says global PATs retire on December 1, 2026](https://devblogs.microsoft.com/devops/retirement-of-global-personal-access-tokens-in-azure-devops/).

## Verify and maintain

- Confirm the live version, README, icon, and changelog on the Marketplace listing.
- Install from the Marketplace in a clean VS Code environment and repeat a static-analysis and profile run.
- If you published native packages, test the Rust backend on each platform you support.
- For each later release, use a new version number, update the changelog, rerun tests, and upload a newly reviewed VSIX.

The [detailed deployment reference](../guide-deploy.md) includes additional platform build examples. Check any authentication steps there against the current official publishing documentation before a release.

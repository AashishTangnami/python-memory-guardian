# Publish to the VS Code Marketplace

This guide is for maintainers preparing a public Marketplace release. Publishing changes an external listing; finish the package and review it before uploading. For package construction, see [local deployment](05-local-deploy.md). This checkout does **not** include an automated release workflow.

## One-time preparation

1. Create a publisher through the [Marketplace management page](https://marketplace.visualstudio.com/manage).
2. Replace `your-publisher-id` in `package.json` with that publisher's ID.
3. Confirm that the repository, bugs, and homepage URLs in `package.json` still match the Git remote.
4. Check the license, icon, README, and changelog. Replace the placeholder extension ID in the example Dev Container configuration if you want users to install from its Marketplace ID.

The published extension ID is `<publisher>.python-memory-guardian`. This checkout uses the Git remote for its repository URLs, but the manifest's publisher ID is still a placeholder and must match a publisher you control before a real release.

## Prepare a release

1. Confirm the version in `package.json` and `package-lock.json` is higher than the published version, and finalize its `CHANGELOG.md` section. Set the same version in `rust-server/Cargo.toml` (the Rust server reports it at initialization) and in the `LanguageServer(...)` call in `server/guardian_server.py`. This checkout is prepared as 1.4.1; verify that number against the Marketplace listing before publishing.
2. Build and run the project tests:

   ```bash
   npm ci
   npm run build:rust
   npm test
   ```

   Rust is needed to verify both static-analysis backends. If you only distribute the Python backend, the Rust build is optional, but `npm run test:parity` will otherwise skip the Rust comparison.

   Review both `npm audit` and `npm audit --omit=dev`. The full audit includes the development-only VSIX packaging tool; the production-only audit checks the Node dependency tree shipped with the extension. Do not treat a production-only clean result as a fix for a vulnerable build tool.

3. Build the universal VSIX:

   ```bash
   npx vsce package
   ```

4. Check the package contents with `npx vsce ls --tree`. Run `python test-fixtures/package_test.py path/to/package.vsix` with the oldest and current supported Python, then install the VSIX in a clean VS Code environment and test warnings and profiling. Use the [local deployment checklist](05-local-deploy.md#install-and-check-it).

Build platform-specific VSIXs separately if you are distributing the Rust backend. Each package must contain a binary for its target OS and CPU. The [VS Code publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#platform-specific-extensions) explains `--target` packages and the universal fallback.

## 1.4.1 preparation status (October 4, 2026)

| Gate | Status in this checkout |
|---|---|
| Version and notes | `package.json`, `package-lock.json`, `rust-server/Cargo.toml` and `Cargo.lock`, and `server/guardian_server.py` are all 1.4.1; `CHANGELOG.md` has a 1.4.1 section. Both language servers report 1.4.1 in their `initialize` response. Confirm that 1.4.1 is newer than the live Marketplace version before upload. |
| Source and runtime tests | `npm test` and the Rust-required parity check (`PMG_REQUIRE_RUST=1`) pass on Python 3.13.0 with a fresh macOS ARM release binary. Not yet rerun on Python 3.9. |
| Packaging, clean install and audit | Not yet run for 1.4.1: build the VSIX, run the packaged Python-server smoke test, install it in a clean VS Code profile, and rerun `npm audit`. The publisher ID and `LICENSE` copyright holder are still placeholders, as in 1.4.0. |

The 1.4.0 table below is kept as the record of that release's checks.

## 1.4.0 preparation status (October 3, 2026)

| Gate | Status in this checkout |
|---|---|
| Version and notes | `package.json` and `package-lock.json` are both 1.4.0; `CHANGELOG.md` has a 1.4.0 section. Confirm that 1.4.0 is newer than the live Marketplace version before upload. |
| Publisher and legal identity | Repository URLs match the configured Git remote. The publisher ID in `package.json` and the copyright holder in `LICENSE` are still placeholders; confirm their exact values with the owner. |
| Source and runtime tests | `npm test` passes on Python 3.13.0. Python/Rust parity and runtime tests pass on Python 3.9.6; two Python 3.12+ monitoring cases are skipped there. |
| Universal package | A 1.4.0 VSIX built with the pinned packager and passed the packaged Python-server smoke test on Python 3.13.0. A pre-version-bump VSIX also passed on Python 3.9.6. The manifest still has a placeholder publisher; rebuild and test the final VSIX after setting the real publisher. |
| Rust package | A fresh macOS ARM release binary builds and passes parity. A matching platform VSIX has not been produced or smoke-tested for 1.4.0. Other target platforms require their own builds. |
| Clean VS Code install | Pending the final publisher-specific 1.4.0 VSIX; verify static diagnostics and profiling in a clean VS Code profile before upload. |
| VS Code API compatibility | `engines.vscode` remains `^1.82.0`, and `@types/vscode` is pinned to `1.82.0`. The previous `^1.140.0` types declaration failed `vsce package`'s compatibility check. Compilation and `npx vsce package` pass after aligning them. |
| npm dependency audit | On October 3, `npm ci` and `npm audit` report zero findings with `@vscode/vsce@4.0.0`; the packaged-server smoke test passes on Python 3.13.0. The earlier `3.9.2` packager pulled in `secretlint → globby → fast-glob → micromatch → braces@3.0.3`, producing six high-severity entries for one [unpatched `braces` advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). Re-audit and package-test after any lockfile update. |
| Install deprecations | The current clean install emits no deprecation warnings. Earlier packager versions warned about `whatwg-encoding`, `prebuild-install`, and `glob`. |
| Source control | `AGENTS.md`, `MEMORY.md`, and this guide are committed. The `AGENTS.md` reference to `.agents/skills/maintain-feature-docs/SKILL.md` must be included in the next commit; `.gitignore` now allows the skill to be tracked. |

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

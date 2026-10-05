# Build, install and release

This guide covers packaging Python Memory Guardian as a `.vsix`, installing it locally, in containers or for a team, and publishing it to the VS Code Marketplace. For building and running the code during development, see [developer setup](04-developer-setup.md).

> ⚠️ **Publishing authentication is changing.**
> Microsoft retires *global* Azure DevOps Personal Access Tokens (PATs) on **December 1, 2026**. Global PATs are the only PATs that can publish to the Marketplace. After that date, `vsce publish` with a PAT stops working.
> [Choose how you will authenticate](#choose-how-you-will-authenticate) lists the methods that keep working. Check the [official publishing page](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) for updates, since this area is moving.

**Contents:** [What you are building](#what-you-are-building) · [Build a package](#build-a-package) · [Install it](#install-it) · [Prepare a release](#prepare-a-release) · [Publish](#publish-to-the-marketplace) · [Release updates](#release-updates) · [Troubleshooting](#troubleshooting) · [Release records](#release-records)

## What you are building

The extension ships as **`.vsix` files** (zip archives that VS Code installs). There are two kinds:

| Package | Contains | Built with | Who it's for |
|---|---|---|---|
| **Universal** | TypeScript client (bundled), Python server, profiler, bundled `pygls` | `npx vsce package` | Everyone. The Python backend works on every OS |
| **Platform-specific** | everything above **plus** the Rust server binary for one OS/CPU | `npx vsce package --target <platform>` | Users who choose the Rust backend |

You can ship the universal package alone. The Rust backend then isn't available, but everything else works; users need Python 3.9+ on the machine where the extension runs. If you also publish platform-specific packages, the Marketplace gives each user the one matching their machine, and uses the universal package as the fallback for every other platform.

Valid platforms: `win32-x64`, `win32-arm64`, `linux-x64`, `linux-arm64`, `linux-armhf`, `alpine-x64`, `alpine-arm64`, `darwin-x64`, `darwin-arm64`. To find your own machine's platform:

```bash
node -p "process.platform + '-' + process.arch"   # e.g. linux-x64, darwin-arm64, win32-x64
```

On Alpine Linux, use `alpine-x64` or `alpine-arm64` instead of `linux-*`.

### Prerequisites

These tools are needed to **build** a `.vsix` from the source repository. If you already have a `.vsix` file, skip to [Install it](#install-it); you only need VS Code and Python 3.9+ to use the extension.

| Tool | Check | Notes |
|---|---|---|
| Node.js 22+ | `node --version` | required by the pinned `@vscode/vsce@4.0.0` packager |
| Python 3.9+ | `python3 --version` | used to run the backend checks |
| uv | `uv --version` | packages and bundles the pinned `pygls` dependencies |
| Internet access | | packaging downloads the packages pinned in `requirements.txt` from PyPI |
| Rust 1.80+ | `cargo --version` | **only** for platform-specific packages |
| VS Code CLI | `code --version` | for command-line installs. If missing, run **Shell Command: Install 'code' command in PATH** in VS Code (macOS) |

`vsce` itself doesn't need a global install: the project has it as a dev dependency, so `npx vsce …` works after `npm ci`.

## Build a package

### Universal package

Run these commands from the repository root, the folder containing `package.json`:

```bash
npm ci                     # exact dependency versions from package-lock.json
npx vsce package           # -> python-memory-guardian-<version>.vsix
```

`npm run package` runs the same command. `vsce package` automatically runs the `vscode:prepublish` script first. That script bundles the version-pinned packages from `requirements.txt` into `server/libs/`, and bundles the TypeScript client into `dist/extension.js`. A successful run ends with:

```
 DONE  Packaged: python-memory-guardian-<version>.vsix
```

It doesn't matter which Python version you package with. `requirements.txt` pins versions compatible with the oldest supported Python (3.9) and lists every dependency explicitly, so the bundle is consistent and works on 3.9 through 3.14. The `vendor:python` script uses uv with Python 3.9 as its target. Don't replace it with an unpinned install of `pygls`; that can select dependencies incompatible with Python 3.9 and 3.10.

To see exactly what went into the package:

```bash
npx vsce ls --tree
```

The client and its npm dependencies (such as `vscode-languageclient`) are bundled by esbuild into `dist/extension.js`. That's why `node_modules/` is excluded from the package on purpose through `.vscodeignore`. If `dist/extension.js` is missing, the extension can't start, so always let `vsce package` run its prepublish step. Generated root-level VSIX and source-archive files are ignored by Git.

### Build and install in one step (macOS, Linux)

For a local development install, run the helper instead:

```bash
node scripts/install-local.js
```

It runs `npm ci`, packages the current manifest version (including the Python vendoring and TypeScript prepublish steps), then runs `code --install-extension <vsix> --force`. It stops if any step fails. Run `node scripts/install-local.js --dry-run` to print the steps without changing anything. Set `PMG_CODE_CLI=code-insiders` if you use VS Code Insiders; the normal `code` CLI must otherwise be on `PATH`. Reload the VS Code window after installation. This installs into the local VS Code profile; use the appropriate remote window or CLI for a remote extension host. On Windows, use the manual steps on this page.

### Optional: a package with the Rust backend for your machine

```bash
npm run build:rust
mkdir -p bin
cp rust-server/target/release/guardian-server bin/        # Windows: copy ...\guardian-server.exe bin\
npx vsce package --target linux-x64                       # use YOUR platform from above
```

The result is `python-memory-guardian-linux-x64-<version>.vsix`. Only install it on a matching machine. A VSIX for a Dev Container must match the **container's** platform. Delete `bin/` before building the universal package again, so the binary doesn't end up in it. To build for other platforms, see [Rust packages for other platforms](#rust-packages-for-other-platforms).

## Install it

Use this section after building a package, or when someone has given you a `.vsix` file. You do not need Node.js, uv, or Rust just to install an existing package.

**From the VS Code interface:**
1. Open the Extensions view (**Ctrl+Shift+X**, or **Cmd+Shift+X** on macOS).
2. Click the **⋯** menu (*Views and More Actions*), then **Install from VSIX…**. The Command Palette's **Extensions: Install from VSIX…** does the same.
3. Pick the `.vsix` file and reload the window when prompted.

**From the command line:**

```bash
code --install-extension python-memory-guardian-<version>.vsix
```

### Check that it works

1. Open a folder that contains Python files. The repository's `test-fixtures/` folder works well.
2. Open `native_patterns.py`. Warnings should appear within a second.
3. Open **View → Output → Python Memory Guardian**. You should see an `Interpreter profile: {...}` line.
4. Open `test-fixtures/profiler/holders_workload.py`, run **Python Memory Guardian: Profile Current File**, and choose **precise**. The inline labels should include `held by Service.history`.
5. If you installed a platform-specific package, set `"pythonMemoryGuardian.backend": "rust"` and repeat step 2.

**To test in a clean environment,** without your other extensions or settings:

```bash
code --user-data-dir /tmp/pmg-clean --extensions-dir /tmp/pmg-ext \
     --install-extension python-memory-guardian-<version>.vsix
code --user-data-dir /tmp/pmg-clean --extensions-dir /tmp/pmg-ext test-fixtures
```

On Windows, use folders such as `%TEMP%\pmg-clean` and `%TEMP%\pmg-ext`.

### Install into containers and remote machines

The extension runs where your code runs: in the container, WSL or the SSH host. It must therefore be installed **there**, not just on your laptop.

| Setup | How to install a local `.vsix` there |
|---|---|
| Dev Container / Codespace | Open the container window, then use **Extensions → ⋯ → Install from VSIX…** in that window. Alternatively, run `code --install-extension /path/in/container/file.vsix` in its integrated terminal |
| WSL / Remote-SSH | Same: install from the remote window, or with `code --install-extension` in the remote terminal |
| Plain Docker (editor on host) | Install normally on the host. The extension stays on the host and reaches into the container through [container mode](03-container-setup.md#docker-or-compose-with-vs-code-on-the-host) |

The `.vsix` file has to be reachable from the remote side. Put it inside the project folder, or copy it in (e.g. `docker cp file.vsix <container>:/tmp/`).

`devcontainer.json`'s `"extensions"` list only accepts Marketplace IDs, not `.vsix` paths. Until the extension is on the Marketplace, install the `.vsix` manually as above after the container starts.

For the Rust backend in a container, see the [container notes](03-container-setup.md#dev-containers-and-codespaces).

### Share with your team without the Marketplace

1. Build the packages you need (universal, and one per platform for the Rust backend).
2. Put them where your team can download them: a GitHub Release, a shared drive, or an internal artifact store.
3. Teammates install them as above.

Installs from a `.vsix` **don't auto-update**. Announce new versions, and have teammates install the new file.

### Update or uninstall a local install

```bash
code --install-extension python-memory-guardian-<version>.vsix          # newer version replaces older
code --install-extension python-memory-guardian-<version>.vsix --force  # reinstall the same version
code --uninstall-extension your-publisher-id.python-memory-guardian
code --list-extensions --show-versions | rg python-memory-guardian
```

The ID is `<publisher>.<name>` from `package.json`.

## Prepare a release

### Create a publisher (one time)

A publisher is your identity on the Marketplace. Every extension belongs to one.

1. Sign in with a Microsoft account at **https://marketplace.visualstudio.com/manage**.
2. Click **Create publisher**.
3. Fill in:
   - **ID:** lowercase and unique, for example `acme-tools`. It appears in the extension's URL and **can never be changed**.
   - **Name:** the display name shown on your listing; also unique.
4. Click **Create**.

### Replace the placeholders (one time)

The published extension ID is `<publisher>.python-memory-guardian`. The manifest's publisher ID is still a placeholder and must match a publisher you control before a real release.

| File | Change |
|---|---|
| `package.json` → `publisher` | `your-publisher-id` → your publisher **ID** |
| `package.json` → `repository`, `bugs`, `homepage` | Confirm the URLs still match the configured Git remote. |
| `LICENSE` | `<copyright holder>` → your name or organization (and change the license if MIT isn't what you want; update `license` in `package.json` to match) |
| `examples/containerized-app/.devcontainer/devcontainer.json` | `your-publisher-id.python-memory-guardian` → `<your ID>.python-memory-guardian` |
| `README.md` | the same extension ID wherever it appears |

Find any you missed:

```bash
rg -n --hidden --no-ignore -g '!**/.git/**' -g '!node_modules/**' -g '!rust-server/target/**' 'your-publisher-id|your-org|<copyright holder>' .
```

### Release steps

1. **Set the version** in all four places, higher than the published version: `package.json` and `package-lock.json`, `rust-server/Cargo.toml` (the Rust server reports it at initialization; `Cargo.lock` follows on the next build), and the `LanguageServer(...)` call in `server/guardian_server.py`. Finalize its `CHANGELOG.md` section, which the Marketplace shows.
2. **Build and test:**

   ```bash
   npm ci
   npm run build:rust
   npm test
   PMG_REQUIRE_RUST=1 npm run test:parity
   ```

   Rust is needed to verify both static-analysis backends. If you only distribute the Python backend, the Rust build is optional, but `npm run test:parity` will otherwise skip the Rust comparison. If you changed `requirements.txt`, also run `npm run vendor:python && npm run test:parity` on Python 3.9 (`PMG_PYTHON=/path/to/python3.9`).
3. **Audit dependencies.** Review both `npm audit` and `npm audit --omit=dev`. The full audit includes the development-only VSIX packaging tool; the production-only audit checks the Node dependency tree shipped with the extension. Do not treat a production-only clean result as a fix for a vulnerable build tool.
4. **Build the packages**: the universal VSIX, and platform VSIXs if you distribute the Rust backend.
5. **Check the package.** Run `npx vsce ls --tree`, then `python test-fixtures/package_test.py path/to/package.vsix` with the oldest and the current supported Python; it checks runtime files, development-file exclusions, and real LSP diagnostics from the extracted server. Install the VSIX in a clean VS Code profile and repeat [Check that it works](#check-that-it-works).
6. **Run the checklist below**, then publish.

| Check | How |
|---|---|
| Version is new | `version` in `package.json` must be higher than anything already published. A number can never be reused, even after you delete that version |
| `CHANGELOG.md` updated | it's shown on the Marketplace page |
| Name is free | search the Marketplace for "Python Memory Guardian". Both `name` and `displayName` must be unique Marketplace-wide |
| Icon is a PNG | `images/icon.png` (256×256 is included). SVG icons are rejected |
| README images | must be `https://` URLs, and not SVG (except trusted badge providers). The current README has no images |
| At most 30 `keywords` | more fails with "exceeded the number of allowed tags of 30" |
| No packaging warnings | `npx vsce package` should print `DONE` with no `WARNING` lines |
| VS Code API compatibility | keep `@types/vscode` pinned to the minimum `engines.vscode` version; `vsce package` checks this match |

## Publish to the Marketplace

### Choose how you will authenticate

| Method | Works after Dec 1, 2026? | Best for | Section |
|---|---|---|---|
| **A. Manual upload** in the browser | ✅ yes (normal Microsoft sign-in, no token) | first release, occasional releases, single maintainer | [A](#method-a-manual-upload-in-the-browser) |
| **B. `vsce publish` with a PAT** | ❌ **no**, global PATs are retired that day | quick command-line publishing until then | [B](#method-b-vsce-publish-with-a-personal-access-token) |
| **C. CI with Microsoft Entra ID** | ✅ yes (Microsoft's recommended method) | automated releases, teams | [C](#method-c-automated-releases-with-github-actions-and-entra-id) |
| *D. Trusted Publishing (`vsce publish --oidc`)* | ✅ designed for it | the simplest CI setup **once available** | [D](#method-d-trusted-publishing-not-available-yet) |

**Recommendation:** use **A** for the first release. Set up **C** for ongoing releases if you publish regularly, or keep using **A**. Avoid building anything new on **B**. This repository has no `.github/workflows/release.yml`, so publishing is manual until a workflow is created and tested.

### Method A: manual upload in the browser

**First release:**
1. Go to **https://marketplace.visualstudio.com/manage** and select your publisher.
2. Click **New extension → Visual Studio Code**.
3. Upload `python-memory-guardian-<version>.vsix`.
4. Wait while it's verified (usually a few minutes). The status changes when it's live.

**Later releases:** on the same page, open the extension's **⋯** menu, choose **Update**, and upload the new `.vsix`.

**Platform-specific packages:** upload the universal package first, then each platform-specific one. The target platform is recorded inside each `.vsix` (`TargetPlatform="linux-x64"` in its manifest). If the web page refuses an additional package for a version that already exists, publish the platform packages with `vsce` (method B before December 1, 2026, or method C).

The [official publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) documents the current upload flow and listing constraints.

### Method B: `vsce publish` with a Personal Access Token

> ⏳ Only until **December 1, 2026**. Don't build long-term automation on this.

1. **Create the token.** Go to https://dev.azure.com and create an organization if you have none. Open **User settings → Personal access tokens → New Token** and set:
   - **Organization:** **All accessible organizations** (required; a single organization gives 401/403 errors)
   - **Scopes:** **Custom defined → Show all scopes → Marketplace → Manage**
   - **Expiration:** your choice, but publishing stops working on December 1, 2026 regardless

   Copy the token now; it's shown only once.
2. **Log in once:**
   ```bash
   npx vsce login <your-publisher-id>        # paste the token when asked
   ```
3. **Publish:**
   ```bash
   npx vsce publish                                                   # builds and publishes the universal package
   npx vsce publish --packagePath python-memory-guardian-linux-x64-<version>.vsix   # each prebuilt platform package
   ```
   All packages of one release must carry the same version number. Build every platform package first, then publish them all, and only then bump the version for the next release.

**Headless Linux** (servers, containers, CI): `vsce login` stores the token with `libsecret`, which often isn't available there. Skip `login` and pass the token through the environment instead:

```bash
VSCE_PAT=<token> npx vsce publish
```

### Rust packages for other platforms

| Rule | Why |
|---|---|
| **Linux: build static musl binaries** (`x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`) | A normal (glibc) build from a recent distribution requires that glibc version or newer. The binary from Ubuntu 24.04 needs **glibc 2.34+**, so it fails on Ubuntu 20.04, Debian bullseye (2.31), RHEL 8 (2.28) and Amazon Linux 2 (2.26), all common in containers. A musl binary runs on every Linux, and the same file serves both `linux-*` and `alpine-*` |
| **Windows: build with `RUSTFLAGS="-C target-feature=+crt-static"`** | otherwise the `.exe` needs the Visual C++ runtime DLL, which some machines lack |
| **Package Linux and macOS targets on Linux or macOS** | packaging on Windows drops the executable bit. The extension restores it at startup as a safety net, but don't rely on that |
| **One `vsce package --target …` per binary** | each package may contain only its own platform's binary in `bin/` |

Example for static Linux x64, on a Linux machine with Rust installed through `rustup`:

```bash
rustup target add x86_64-unknown-linux-musl
sudo apt-get install -y musl-tools        # Debian/Ubuntu
cargo build --release --manifest-path rust-server/Cargo.toml --target x86_64-unknown-linux-musl
rm -rf bin && mkdir bin && cp rust-server/target/x86_64-unknown-linux-musl/release/guardian-server bin/
npx vsce package --target linux-x64
npx vsce package --target alpine-x64      # same static binary, second package
```

Building all nine targets by hand is tedious. Method C outlines an automated approach, but this checkout does not include that workflow. The [VS Code publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#platform-specific-extensions) explains `--target` packages and the universal fallback.

### Method C: automated releases with GitHub Actions and Entra ID

This checkout does **not** include `.github/workflows/release.yml`. The workflow described below is a proposed setup; create and test it before using tag-triggered releases. A completed workflow should:

1. run the full test suite;
2. build the universal package and the platform packages you choose to support;
3. publish them with an authentication method supported by the Marketplace.

Microsoft's official instructions for identity-based publishing are written for **Azure Pipelines**. The GitHub Actions variant below follows the same model and is used by public projects (for example `github/vscode-codeql`), but it **has not been run as part of writing this guide**. Test it with a pre-release first (see [Pre-releases](#pre-releases)).

**One-time setup:**

1. **Create a managed identity in Azure.** In the Azure portal, create a **user-assigned managed identity** and give it the **Reader** role on its resource group. You need an Azure subscription; this costs nothing by itself. Note its **Client ID**, **Tenant ID** and **Subscription ID**.
2. **Trust your GitHub workflow.** On the identity, add a **Federated credential** of type *GitHub Actions deploying Azure resources*:
   - **Organization / Repository:** your GitHub repository
   - **Entity:** *Environment*, named `marketplace` (matches `environment: marketplace` in the workflow)

   This produces issuer `https://token.actions.githubusercontent.com` and subject `repo:<owner>/<repo>:environment:marketplace`.
3. **Find the identity's Marketplace profile ID.** Run this once from any workflow or shell that is logged in *as that identity* (for example, a temporary job in the same workflow after `azure/login`):
   ```bash
   az rest -u https://app.vssps.visualstudio.com/_apis/profile/profiles/me \
           --resource 499b84ac-1321-427f-aa17-267ca6975798
   ```
   Copy the `id` field from the output.
4. **Add the identity to your publisher.** On https://marketplace.visualstudio.com/manage, select your publisher, open **Members**, add that `id`, and give it the **Contributor** role.
5. **Configure the GitHub repository:**
   - Under **Settings → Environments**, create `marketplace`. Add *required reviewers* if you want a manual approval before each publish.
   - Under **Settings → Secrets and variables → Actions → Variables**, add `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`. These are identifiers, not secrets, so *Variables* is correct.
6. **Commit `package-lock.json`.** The workflow uses `npm ci`, which needs it.

**Each release:**

```bash
# 1. bump "version" (see Release steps) and update CHANGELOG.md, then:
git commit -am "Release <version>"
git tag v<version>
git push origin main v<version>
```

Only after creating the workflow and testing it, watch its Actions run for the tag. If you configured reviewers, approve the publish job when it asks.

### Method D: Trusted Publishing (not available yet)

`vsce publish --oidc` is designed to publish from GitHub Actions with **no Azure setup at all**: you register a trust policy for your repository and workflow on the Marketplace. The [vsce README](https://github.com/microsoft/vscode-vsce#trusted-publishing) documents it. But according to a public report from September 28, 2026, the Marketplace still rejected it then, with Microsoft saying on September 14 that the feature was still in development.

**When the policy setting appears** on your publisher's management page:
1. Register the policy for your repository and `release.yml`.
2. In the workflow's `publish` job, delete the `azure/login` step.
3. Replace `vsce publish --azure-credential` with `vsce publish --oidc` (vsce 4.0+). Keep `id-token: write`.

### Check the release

1. Open `https://marketplace.visualstudio.com/items?itemName=<publisher>.python-memory-guardian`. Check the version, icon, README and changelog.
2. In a clean VS Code (see [Check that it works](#check-that-it-works)), search the Extensions view for *Python Memory Guardian* and install it from the Marketplace.
3. Repeat the checks, including `"pythonMemoryGuardian.backend": "rust"` on each platform you published.
4. In a Dev Container (e.g. `examples/containerized-app`), confirm the extension installs **inside** the container from its Marketplace ID.

## Release updates

### Version numbers

- Use `major.minor.patch` only. The Marketplace doesn't accept semver tags like `-beta`.
- Every upload needs a version number that has never been used before.
- `npx vsce publish patch` (or `minor`, `major`, or an explicit `1.4.0`) bumps `package.json`, commits, tags, and publishes in one step (method B). It does not update `Cargo.toml` or `guardian_server.py`; set those first.

### Pre-releases

Users can opt into pre-release versions:

```bash
npx vsce package --pre-release
npx vsce publish --pre-release
```

VS Code always updates users to the highest version, so pick a numbering scheme. Microsoft suggests an **even** minor for releases and an **odd** minor for pre-releases, for example `1.4.x` stable and `1.5.x` pre-release.

### Unpublish or remove

| Action | Effect | Reversible? |
|---|---|---|
| **Unpublish** (manage page → ⋯ → Unpublish) | hidden from search and new installs; statistics kept | ✅ yes |
| **Delete a version** (⋯ → Reports → Manage → Delete this version) | that version is gone; its number can't be reused; the latest version can't be deleted | ❌ no |
| **Remove** (`npx vsce unpublish <publisher>.python-memory-guardian`, or ⋯ → Remove) | extension and statistics deleted; **its name is reserved forever**, even for you | ❌ no |

Prefer *unpublish* unless you're certain.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `WARNING A 'repository' field is missing` / `LICENSE … not found` and a `[y/N]` prompt | placeholders were removed or files deleted; restore `repository` in `package.json` and the `LICENSE` file |
| `401 Unauthorized` / `403 Forbidden` on `vsce publish` | PAT not created for **All accessible organizations**, or missing the **Marketplace → Manage** scope. After December 1, 2026: PATs no longer work, so use method A or C |
| `The extension '…' already exists in the Marketplace` | `name` or `displayName` is taken by someone else; choose another |
| `You exceeded the number of allowed tags of 30` | too many `keywords` in `package.json` |
| SVG icon or image rejected | use PNG; README images must be `https://`, non-SVG |
| `Rust server binary not found` after installing | the user got the universal package (no binary). Publish a platform package for their platform, or have them use the Python backend |
| `version 'GLIBC_2.34' not found` in the extension's output | Linux binary built against glibc; rebuild with a musl target |
| `EACCES` / permission denied starting the Rust server | package built on Windows lost the executable bit, on a read-only install location where the extension can't fix it. Rebuild on Linux or macOS |
| Extension fails to activate: `Cannot find module …/dist/extension.js` | packaged without the bundle step. Run `npm run compile` (or let `vsce package` run `vscode:prepublish`), and check `dist/extension.js` exists |
| Python backend fails on 3.9/3.10 with `No module named 'exceptiongroup'` | the bundle was built without `requirements.txt` (e.g. an old `vendor:python` script). Run `npm run vendor:python` and package again |
| `server/libs/` missing after packaging | the prepublish step did not run or failed; run `npm run vendor:python` and check its output |
| `vsce login` fails on Linux with a keyring / `libsecret` error | use `VSCE_PAT=<token> npx vsce publish` instead of `login` |
| CI: `azure/login` fails with `AADSTS70021` / no matching federated identity | the federated credential's subject doesn't match. It must be `repo:<owner>/<repo>:environment:marketplace` for this workflow |
| CI: publish fails with 401 after login succeeds | the identity's profile `id` isn't a **Contributor** member of your publisher (method C, steps 3–4) |
| `TrustedPublishingNotSupportedException` | method D isn't available yet; use method C |
| Pre-release users jump to a stable version | expected: VS Code installs the highest version. Use the even/odd scheme above |
| More help | sign in at https://marketplace.visualstudio.com/manage and use **Contact Microsoft** (top right) |

## Release records

### Verification of this guide's procedures (October 1, 2026)

**Run:**
- `npx vsce package`: universal package built with no warnings (160 files, 494.79 KB).
- `npx vsce package --target linux-x64`: built, with the Rust binary present and executable (`-rwxr-xr-x`) and `TargetPlatform="linux-x64"` in the manifest.
- The bundled `dist/extension.js` loads and exports `activate`.
- The glibc requirement of a glibc-built binary was measured (2.34).

**Not run:** an actual Marketplace upload or publish (it needs a real publisher account); a GitHub Actions release workflow, including the musl, macOS and Windows builds; the Entra ID setup.

### 1.4.2 (October 4, 2026)

| Gate | Status |
|---|---|
| Version and notes | `package.json`, `package-lock.json`, `rust-server/Cargo.toml` and `Cargo.lock`, and `server/guardian_server.py` are all 1.4.2; `CHANGELOG.md` has a 1.4.2 section. Both language servers report 1.4.2 in their `initialize` response (the Rust server after a release rebuild). Confirm that 1.4.2 is newer than the live Marketplace version before upload. |
| Source and runtime tests | `npm test` and the Rust-required parity check (`PMG_REQUIRE_RUST=1`) pass on Python 3.13.0 with a freshly built macOS release binary; `profiler_regression_test.py` also passes on Python 3.12 and 3.9.6 (3 line-event tests skip on 3.9). |
| Packaging, clean install and audit | Not yet run for 1.4.2: build the VSIX, run the packaged Python-server smoke test, install it in a clean VS Code profile, and rerun `npm audit`. The publisher ID and `LICENSE` copyright holder are still placeholders, as in 1.4.1. |

### 1.4.1 (October 4, 2026)

| Gate | Status |
|---|---|
| Version and notes | All four version locations and `Cargo.lock` are 1.4.1; `CHANGELOG.md` has a 1.4.1 section. Both language servers report 1.4.1 in their `initialize` response. |
| Source and runtime tests | `npm test` and the Rust-required parity check pass on Python 3.13.0 with a fresh macOS ARM release binary. Not rerun on Python 3.9. |
| Packaging, clean install and audit | Not run for 1.4.1. The publisher ID and `LICENSE` copyright holder are still placeholders, as in 1.4.0. |

### 1.4.0 (October 3, 2026)

| Gate | Status |
|---|---|
| Version and notes | `package.json` and `package-lock.json` are both 1.4.0; `CHANGELOG.md` has a 1.4.0 section. |
| Publisher and legal identity | Repository URLs match the configured Git remote. The publisher ID in `package.json` and the copyright holder in `LICENSE` are still placeholders; confirm their exact values with the owner. |
| Source and runtime tests | `npm test` passes on Python 3.13.0. Python/Rust parity and runtime tests pass on Python 3.9.6; two Python 3.12+ monitoring cases are skipped there. |
| Universal package | A 1.4.0 VSIX built with the pinned packager and passed the packaged Python-server smoke test on Python 3.13.0. A pre-version-bump VSIX also passed on Python 3.9.6. The manifest still has a placeholder publisher; rebuild and test the final VSIX after setting the real publisher. |
| Rust package | A fresh macOS ARM release binary builds and passes parity. A matching platform VSIX has not been produced or smoke-tested for 1.4.0. Other target platforms require their own builds. |
| Clean VS Code install | Pending the final publisher-specific VSIX; verify static diagnostics and profiling in a clean VS Code profile before upload. |
| VS Code API compatibility | `engines.vscode` remains `^1.82.0`, and `@types/vscode` is pinned to `1.82.0`. The previous `^1.140.0` types declaration failed `vsce package`'s compatibility check. Compilation and `npx vsce package` pass after aligning them. |
| npm dependency audit | On October 3, `npm ci` and `npm audit` report zero findings with `@vscode/vsce@4.0.0`. The earlier `3.9.2` packager pulled in `secretlint → globby → fast-glob → micromatch → braces@3.0.3`, producing six high-severity entries for one [unpatched `braces` advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). Re-audit and package-test after any lockfile update. |
| Install deprecations | The clean install emits no deprecation warnings. Earlier packager versions warned about `whatwg-encoding`, `prebuild-install`, and `glob`. |

### Sources

- [Publishing Extensions](https://code.visualstudio.com/api/working-with-extensions/publishing-extension): PAT retirement, platform-specific packages, constraints.
- [Retirement of global PATs](https://devblogs.microsoft.com/devops/retirement-of-global-personal-access-tokens-in-azure-devops/).
- [vsce README: Trusted publishing](https://github.com/microsoft/vscode-vsce#trusted-publishing).
- Trusted Publishing availability as reported in [japanese-novel/vscode-jpnov#130](https://github.com/japanese-novel/vscode-jpnov/issues/130), which links Microsoft's replies.

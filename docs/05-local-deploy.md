# Build and install a local VSIX

Use this guide to install or share a build without publishing to the Marketplace. A `.vsix` is the installable extension package. The full [deployment reference](../guide-deploy.md) covers more platforms and packaging details.

## Build a universal package

You need Node.js 22+, npm, and `uv` on the build machine. From the repository root:

```bash
npm ci
npx vsce package
```

For a local development install on macOS or Linux, run the single helper instead:

```bash
node scripts/install-local.js
```

It runs `npm ci`, packages the current manifest version (including the Python vendoring and TypeScript prepublish steps), then runs `code --install-extension <vsix> --force`. It stops if any step fails. Run `node scripts/install-local.js --dry-run` to print the steps without changing anything. Set `PMG_CODE_CLI=code-insiders` if you use VS Code Insiders; the normal `code` CLI must otherwise be on `PATH`. Reload the VS Code window after installation. This installs into the local VS Code profile; use the appropriate remote window or CLI for a remote extension host.

`vsce package` runs this project's `vscode:prepublish` script, which vendors the pinned Python server dependencies and builds the TypeScript bundle. It produces a file named like `python-memory-guardian-1.4.2.vsix` for the current manifest version. The universal package supports the default Python backend; users need Python 3.9+ on the machine where the extension runs.

Generated root-level VSIX and source-archive filenames are ignored by Git for every version; the Marketplace publishing guide under `docs/` is a source document to keep with the release changes.

To inspect the package contents before installing:

```bash
npx vsce ls --tree
```

## Optional Rust-backed package

The Rust server is a native binary and must match the machine running the extension. Build it, copy it into `bin/`, and package for that platform:

```bash
npm run build:rust
mkdir -p bin
cp rust-server/target/release/guardian-server bin/
npx vsce package --target linux-x64
```

The example target is Linux x64; choose the correct `--target` for your machine. On Windows, copy `guardian-server.exe` instead. A VSIX for a Dev Container must match the **container's** platform. Before building a universal package, make sure `bin/` contains no native binary you intended only for a platform package.

## Install and check it

In VS Code, run **Extensions: Install from VSIX…**, select the package, and reload. Or use the CLI:

```bash
code --install-extension python-memory-guardian-1.4.2.vsix
```

Open a folder with Python files and check that warnings appear. Select a Python interpreter in settings, then profile a runnable script. If you built a platform-specific package, set `pythonMemoryGuardian.backend` to `rust` and check that diagnostics still appear.

For Dev Containers, Codespaces, WSL, and Remote-SSH, install the VSIX in the **remote VS Code window**. For host-side Docker/Compose, install it on the host and use [container mode](03-container-setup.md).

## Share or update

You can give teammates the VSIX through a release artifact or shared storage; they install it using the same steps. Local VSIX installs need an explicit reinstall when you provide a newer build. The installed extension ID comes from `publisher` and `name` in `package.json`.

If packaging or activation fails, check that `server/libs/` and `dist/extension.js` were created by the prepublish step. See the [full deployment troubleshooting section](../guide-deploy.md#part-5-troubleshooting).

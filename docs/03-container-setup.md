# Container and remote setup

Choose the setup that matches where VS Code runs the extension and where Python runs your project.

| Your setup | Where the extension runs | What to do |
|---|---|---|
| **Dev Containers** ("Reopen in Container") or **GitHub Codespaces** | inside the container (automatic) | [Dev Containers and Codespaces](#dev-containers-and-codespaces): no special settings |
| **WSL** or **Remote-SSH** | on the remote machine (automatic) | [WSL and Remote-SSH](#wsl-and-remote-ssh): same as local |
| **Docker / Compose**, with VS Code editing on the host and the app running in a container | on the host | [Docker or Compose with VS Code on the host](#docker-or-compose-with-vs-code-on-the-host): set *container mode* |

The extension declares `"extensionKind": ["workspace"]`, so in remote setups VS Code installs and runs it **next to your code and interpreter**, not on your laptop. Host-side Docker/Compose is the one case that needs explicit container mode. To install an unpublished `.vsix` in a container or remote machine, see [build, install and release](05-build-and-release.md#install-into-containers-and-remote-machines).

## Dev Containers and Codespaces

There's a working example in [`examples/containerized-app/`](../examples/containerized-app/).

1. Install the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).
2. Add the extension to your project's `.devcontainer/devcontainer.json`, and point it at the container's Python (3.9+):
   ```jsonc
   {
     "dockerComposeFile": "../compose.yaml",   // or "image": "python:3.12-slim"
     "service": "app",
     "workspaceFolder": "/app",
     "customizations": {
       "vscode": {
         "extensions": ["<publisher>.python-memory-guardian"],
         "settings": { "pythonMemoryGuardian.interpreter": "/usr/local/bin/python" }
       }
     }
   }
   ```
   The `"extensions"` list accepts only Marketplace IDs. The example's extension ID is still a placeholder; replace it after publishing. For an unpublished `.vsix`, install it in the **container window** instead.
3. Run **Dev Containers: Reopen in Container**.
4. Everything now works as on a local machine. Static analysis, version detection and the profiler all use the container's Python, and paths need no translation. No `container.execPrefix` or path mapping is needed.

**Container notes:**
- The profiler reads memory from `/proc/self/statm` and per-thread CPU clocks, both of which work in standard Linux containers without extra privileges or `--cap-add`.
- **Rust backend in a container:** the bundled binary must match the *container's* OS and CPU, not your laptop's. For example, Docker on an Apple Silicon Mac runs `linux-arm64` containers, and Alpine images need the `alpine-*` build. If in doubt, keep the default Python backend, which only needs Python in the container. Building binaries for each platform is covered in [build, install and release](05-build-and-release.md#rust-packages-for-other-platforms).

## Docker or Compose with VS Code on the host

Use this when you edit files on your machine and the code runs in a container, typically one started by `docker compose up` with your project bind-mounted. In this mode:

- **Static analysis** runs on the host; it only needs the source files. The host needs Python 3.9+ for the default Python backend, or set `pythonMemoryGuardian.backend` to `rust` if its host-platform binary is packaged.
- **Version detection and the profiler** run **inside the container**, through a command prefix you configure.
- **Profile paths** come back as container paths (`/app/...`) and are translated to your host paths.

Step by step, using `examples/containerized-app/`:

1. Make sure the project is bind-mounted into the container, for example in `compose.yaml`:
   ```yaml
   services:
     app:
       build: .
       volumes:
         - .:/app          # host folder  <->  /app inside the container
       command: sleep infinity
   ```
2. Start the container: `docker compose up -d`
3. Create `.vscode/settings.json` in the project (the example ships `settings.example.json`):
   ```jsonc
   {
     "pythonMemoryGuardian.container.execPrefix": ["docker", "compose", "exec", "-T", "app"],
     "pythonMemoryGuardian.container.interpreter": "python",
     "pythonMemoryGuardian.container.pathMappings": [
       { "local": "${workspaceFolder}", "container": "/app" }
     ],
     "pythonMemoryGuardian.backend": "rust"   // only if the HOST has no Python 3.9+
   }
   ```
   - `execPrefix` is the command that runs a program in your container. Use `["docker", "exec", "-i", "my-container"]` for plain Docker. Add `"-f", "path/to/compose.yaml"` after `"compose"` if the compose file isn't at the project root. Keep `-T` with compose: no TTY is needed.
   - `pathMappings` must match the bind mount: `local` is the host folder, `container` is where it appears inside. With several mounts, list each one; the longest match wins.
4. Reload the window (**Developer: Reload Window**). The Output channel should show:
   ```
   Probing container interpreter: docker compose exec -T app python /app/.pmg/probe.py
   Interpreter profile: {"py_version": "3.12.x", ...}
   ```
5. Open `src/service.py` and run **Profile Current File**. The terminal shows the command running inside the container, and results appear on your host files as usual.

**How it works:** the extension copies its two helper scripts (`probe.py`, `pmg_profile.py`, standard library only) into `<project>/.pmg/`. The container already sees that folder through the bind mount, so nothing needs installing in your image. The profiler writes `.pmg/profile.json` there, and the extension maps paths back.

## WSL and Remote-SSH

Open the folder through **WSL: Connect to WSL** or **Remote-SSH: Connect to Host**. The extension runs on that machine; install it in that remote window. Set `pythonMemoryGuardian.interpreter` to a path on the remote machine if needed. Container mode and path mappings are unnecessary.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Output says `Container probe not possible (No pythonMemoryGuardian.container.pathMappings entry covers …)` | `pathMappings` doesn't cover the project folder and the staged `.pmg/` files; check `local` against your bind mount |
| `docker: command not found` in the profile terminal | `docker` isn't on the PATH VS Code sees; use the absolute path in `execPrefix` |
| `service "app" is not running` | start it first: `docker compose up -d` |
| Profile runs but no labels appear | the container path in `pathMappings` doesn't match where the container sees the files. Run `docker compose exec app ls /app` to check |
| Language server fails to start in container mode | the server runs on the **host**: install Python 3.9+ there, or set `"pythonMemoryGuardian.backend": "rust"` |
| Rust backend fails inside a Dev Container | binary built for the wrong OS/CPU (see the container notes above); use a VSIX built for the container platform, or switch to the Python backend |
| A profile is lost when the container stops | `docker stop` sends SIGTERM, which the profiler does not catch. Stop the profiled program with Ctrl+C, or `docker kill -s INT <container>` |

**Verification status:** real Docker, Dev Containers and Codespaces were not available when these steps were written. Container mode was verified with a simulated container (commands through an exec prefix, the project reached through a different path), and the example's `devcontainer.json`, `compose.yaml` and settings files were validated for syntax, not launched. Report anything that differs from these instructions.

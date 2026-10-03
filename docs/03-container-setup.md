# Container and remote setup

Choose the setup that matches where VS Code runs the extension and where Python runs your project.

| Workspace | Extension runs | Setup |
|---|---|---|
| Dev Container or Codespace | In the container | Install the extension there and choose its Python interpreter |
| WSL or Remote-SSH | On the remote machine | Install the extension there and choose its Python interpreter |
| Docker/Compose with VS Code on the host | On the host | Configure container execution and path mappings |

The extension declares `extensionKind: ["workspace"]`, so a remote VS Code workspace normally runs it beside the code. Host-side Docker/Compose is the case that needs explicit container mode.

## Dev Containers and Codespaces

Open the project in its container. Set `pythonMemoryGuardian.interpreter` to a Python 3.9+ executable **inside** it. If the extension is on the Marketplace, you can list its real publisher ID in `.devcontainer/devcontainer.json`:

```jsonc
{
  "customizations": {
    "vscode": {
      "extensions": ["<publisher>.python-memory-guardian"],
      "settings": {
        "pythonMemoryGuardian.interpreter": "/usr/local/bin/python"
      }
    }
  }
}
```

This repository has a runnable example at [`examples/containerized-app/`](../examples/containerized-app/). Its extension ID is still a placeholder; replace it after publishing. For an unpublished `.vsix`, install from VSIX in the **container window** instead of adding a file path to the extension list.

No `container.execPrefix` or path mapping is needed in this setup. A Rust-backed VSIX needs a binary for the **container's** OS and CPU; the Python backend is the default.

## Docker or Compose with VS Code on the host

Use this when VS Code edits host files but the script should run in a container. Bind-mount the project into the container, start the container, then put settings like these in the host workspace:

```jsonc
{
  "pythonMemoryGuardian.container.execPrefix": ["docker", "compose", "exec", "-T", "app"],
  "pythonMemoryGuardian.container.interpreter": "python",
  "pythonMemoryGuardian.container.pathMappings": [
    { "local": "${workspaceFolder}", "container": "/app" }
  ]
}
```

The example assumes a running Compose service named `app` and a mount from the workspace to `/app`. Adjust all three values for your project. For plain Docker, an `execPrefix` such as `["docker", "exec", "-i", "<container>"]` works. The longest matching path mapping is used when several mounts are configured.

Static analysis runs on the host. The extension stages its standard-library probe and profiler scripts in `.pmg/`, runs them through the configured command prefix, and translates profile paths back to host paths. The host needs Python 3.9+ for the default Python static-analysis backend; choose the Rust backend only if its host-platform binary is packaged.

## WSL and Remote-SSH

Open the folder through VS Code's WSL or Remote-SSH connection. Install the extension in that remote window, then set `pythonMemoryGuardian.interpreter` to a path on the remote machine. Container mode and path mappings are unnecessary.

## Troubleshooting

| Symptom | Check |
|---|---|
| `No pythonMemoryGuardian.container.pathMappings entry covers …` | The local mapping must cover the workspace and the staged `.pmg/` files |
| `docker: command not found` | Put the full Docker executable path in `execPrefix`, or fix VS Code's PATH |
| Compose service is not running | Start it with `docker compose up -d` |
| Profile runs but labels are absent | Verify the container mount path and `container` mapping agree |
| Rust server fails in a Dev Container | Use a VSIX built for the container platform, or switch to the Python backend |

For packaging a VSIX for a remote machine, see [local deployment](05-local-deploy.md). The [README container reference](../README.md#2-containerized-projects) has more examples.

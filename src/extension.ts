/**
 * Python Memory Guardian - thin VS Code client.
 *
 * VS Code's extension host only executes JavaScript, so this is the entry
 * point. Analysis runs in an external language server (Python:
 * server/guardian_server.py, or Rust: bin/guardian-server[.exe]).
 *
 * Runtime profiling (profileView.ts) adds measured time/memory per line on top
 * of the static findings, using server/pmg_profile.py.
 *
 * Version awareness: before starting either server, the client runs
 * server/probe.py with the configured interpreter. The probe MEASURES object
 * sizes, the GIL state and the allocator on that interpreter, and the result
 * is passed as initializationOptions.profile so diagnostics quote numbers for
 * the Python the user actually runs. If probing fails, messages fall back to
 * version-neutral wording.
 */
import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";
import { ProfileView } from "./profileView";
import { ContainerConfig, containerCommand, PathMapping, resolveMappings, toContainer } from "./containerPaths";

let client: LanguageClient | undefined;
let output: vscode.OutputChannel | undefined;
let profileView: ProfileView | undefined;
let lifecycle: Promise<void> = Promise.resolve();
let shuttingDown = false;

const RESTART_SETTINGS = ["backend", "interpreter", "container"].map((key) => `pythonMemoryGuardian.${key}`);

function interpreter(): string {
  const configured = vscode.workspace
    .getConfiguration("pythonMemoryGuardian")
    .get<string>("interpreter", "")
    .trim();
  return configured || (process.platform === "win32" ? "python" : "python3");
}

/**
 * Container mode (plain Docker/Compose, editor on the host): set when
 * pythonMemoryGuardian.container.execPrefix is non-empty. Not needed for Dev
 * Containers, Codespaces, WSL or Remote-SSH, where the extension itself runs remotely.
 */
export function containerConfig(): ContainerConfig | undefined {
  const c = vscode.workspace.getConfiguration("pythonMemoryGuardian.container");
  const execPrefix = c.get<string[]>("execPrefix", []).filter((x) => typeof x === "string" && x);
  if (!execPrefix.length) return undefined;
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  return {
    execPrefix,
    interpreter: c.get<string>("interpreter", "python3") || "python3",
    mappings: resolveMappings(c.get<PathMapping[]>("pathMappings", []), ws),
  };
}

/**
 * Copy a helper script (probe.py / pmg_profile.py) into <root>/.pmg/ so a container
 * that bind-mounts the project can run it. Returns the host path of the copy.
 */
export function stageHelper(ctx: vscode.ExtensionContext, root: string, name: string): string {
  const src = ctx.asAbsolutePath(path.join("server", name));
  const dir = path.join(root, ".pmg");
  const dst = path.join(dir, name);
  fs.mkdirSync(dir, { recursive: true });
  const fresh = fs.existsSync(dst) && fs.readFileSync(dst).equals(fs.readFileSync(src));
  if (!fresh) fs.copyFileSync(src, dst);
  return dst;
}

/** Run probe.py on the configured interpreter (or inside the container); {} on failure. */
function probeInterpreter(ctx: vscode.ExtensionContext): Promise<Record<string, unknown>> {
  let command = interpreter();
  let args = [ctx.asAbsolutePath(path.join("server", "probe.py"))];
  const cc = containerConfig();
  if (cc) {
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    try {
      if (!ws) throw new Error("open a folder to use container mode");
      const staged = stageHelper(ctx, ws, "probe.py");
      ({ command, args } = containerCommand(cc, toContainer(staged, cc.mappings), []));
      output?.appendLine(`Probing container interpreter: ${command} ${args.join(" ")}`);
    } catch (e) {
      output?.appendLine(`Container probe not possible (${e}); using version-neutral messages.`);
      return Promise.resolve({});
    }
  }
  return new Promise((resolve) => {
    execFile(command, args, { timeout: 20_000, windowsHide: true,
      cwd: cc ? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath : undefined }, (err, stdout) => {
      if (err) {
        output?.appendLine(`Interpreter probe failed (${err.message}); using version-neutral messages.`);
        return resolve({});
      }
      try {
        const facts = JSON.parse(stdout);
        output?.appendLine(`Interpreter profile: ${JSON.stringify(facts)}`);
        resolve(facts && typeof facts === "object" ? facts : {});
      } catch {
        output?.appendLine("Interpreter probe returned invalid JSON; using version-neutral messages.");
        resolve({});
      }
    });
  });
}

function serverOptions(ctx: vscode.ExtensionContext): ServerOptions {
  const backend = vscode.workspace
    .getConfiguration("pythonMemoryGuardian")
    .get<string>("backend", "python");

  if (backend === "rust") {
    const exe = process.platform === "win32" ? "guardian-server.exe" : "guardian-server";
    const command = ctx.asAbsolutePath(path.join("bin", exe));
    if (!fs.existsSync(command)) {
      throw new Error(`Rust server binary not found at ${command}`);
    }
    if (process.platform !== "win32") {
      // A VSIX packaged on Windows loses the executable bit; restore it if needed.
      try {
        fs.accessSync(command, fs.constants.X_OK);
      } catch {
        fs.chmodSync(command, 0o755);
      }
    }
    return { command, transport: TransportKind.stdio };
  }

  // Python backend: pygls is vendored into server/libs at package time.
  const serverDir = ctx.asAbsolutePath("server");
  return {
    command: interpreter(),
    args: [path.join(serverDir, "guardian_server.py")],
    transport: TransportKind.stdio,
    options: {
      env: { ...process.env, PYTHONPATH: path.join(serverDir, "libs"), PYTHONUTF8: "1" },
    },
  };
}

async function startClient(ctx: vscode.ExtensionContext): Promise<void> {
  const profile = await probeInterpreter(ctx);
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "python" },
      { scheme: "untitled", language: "python" },
    ],
    diagnosticCollectionName: "python-memory-guardian",
    initializationOptions: { profile },
    outputChannel: output,
    // Static findings pass through the runtime profile: hot lines are raised one
    // severity level with measured evidence, cold lines are lowered to hints.
    middleware: {
      handleDiagnostics: (uri, diagnostics, next) =>
        next(uri, profileView ? profileView.adjust(uri, diagnostics) : diagnostics),
    },
  };
  try {
    client = new LanguageClient(
      "pythonMemoryGuardian",
      "Python Memory Guardian",
      serverOptions(ctx),
      clientOptions,
    );
    await client.start();
  } catch (err) {
    client = undefined;
    vscode.window.showErrorMessage(`Python Memory Guardian failed to start: ${err}`);
  }
}

function restartClient(ctx: vscode.ExtensionContext): Promise<void> {
  lifecycle = lifecycle.catch(err => output?.appendLine(`Server stop failed: ${err}`)).then(async () => {
    if (shuttingDown) return;
    await client?.stop();
    client = undefined;
    await startClient(ctx);
  });
  return lifecycle;
}

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  shuttingDown = false;
  output = vscode.window.createOutputChannel("Python Memory Guardian");
  profileView = new ProfileView(ctx, () => client?.diagnostics, interpreter, containerConfig);
  ctx.subscriptions.push(
    output,
    profileView,
    vscode.commands.registerCommand("pythonMemoryGuardian.restart", () => restartClient(ctx)),
    // A new interpreter, backend or container needs a fresh probe and server process. profile.* settings
    // are read by the client only, and the language client applies trace.server changes itself.
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (RESTART_SETTINGS.some((key) => e.affectsConfiguration(key))) {
        void restartClient(ctx);
      } else if (e.affectsConfiguration("pythonMemoryGuardian.profile")) {
        profileView?.settingsChanged();
      }
    }),
  );
  await restartClient(ctx);
}

export function deactivate(): Thenable<void> | undefined {
  shuttingDown = true;
  return lifecycle.catch(() => {}).then(async () => {
    await client?.stop();
    client = undefined;
  });
}

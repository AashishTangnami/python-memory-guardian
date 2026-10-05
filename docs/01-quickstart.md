# Quick start

Use this guide to install Python Memory Guardian and inspect your first Python file in VS Code.

## Requirements

- VS Code 1.82 or newer
- Python 3.9 or newer on the machine where the extension runs
- A folder containing a Python script you can run

The extension bundles its Python server dependencies. You do not need to install them into your project's environment.

## 1. Install

If the extension has been published to the Marketplace, install it from VS Code's Extensions view. Otherwise, obtain a `.vsix` from a maintainer or [build one locally](05-build-and-release.md), then run **Extensions: Install from VSIX…** from the Command Palette and reload VS Code.

For a Dev Container, WSL, or Remote-SSH workspace, install the extension in that remote environment. See [container and remote setup](03-container-setup.md).

## 2. Select the interpreter

Open your project **folder** in VS Code. In Settings, search for `pythonMemoryGuardian.interpreter` and enter the Python executable your code uses, for example `/usr/bin/python3` or the full path to your virtual environment's interpreter. An empty setting uses `python3` on macOS/Linux and `python` on Windows from VS Code's PATH.

The extension probes this interpreter so diagnostics can include measurements from the runtime you actually use.

## 3. See static findings

Open a `.py` file. Diagnostics appear while you edit and on save. Hover over a warning for the reason and suggested change. A warning is a pattern to investigate; it is not proof that your program leaks memory.

If no diagnostics appear, open **View → Output**, select **Python Memory Guardian**, and check for an interpreter or server startup error.

## 4. Run a profile

Save a Python file that can run as a script. Click the pulse icon in the editor title bar or run **Python Memory Guardian: Profile Current File**. Choose **fast** to measure time, or **precise** to find which code keeps memory ([all modes](02-using-the-extension.md#profile-a-script)), then enter the script's arguments, if any.

The script runs in a VS Code terminal. After it finishes, the report opens and measured lines receive inline labels. The profile is written to `.pmg/profile.json` in the project folder. Add `.pmg/` to `.gitignore` if you do not want to commit profiles.

To visualize an existing profile JSON, open it in the editor and click the graph icon in that tab's title bar. The report opens beside the JSON.

## Next

- [Using the extension](02-using-the-extension.md) explains the report, warnings, and settings.
- [Container and remote setup](03-container-setup.md) covers Python running somewhere other than your editor machine.

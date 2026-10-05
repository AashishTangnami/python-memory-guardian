# Profiler command line

The editor command **Profile Current File** runs `server/pmg_profile.py`. You can also run this script from a terminal, for example on a server or in a script. It uses only the Python standard library. You do not have to install the extension.

```
python server/pmg_profile.py [--out FILE] [--root DIR] [--interval SECONDS]
                             [--memory fast|precise|off] [--frames N]
                             [--monitoring off|lines] [--trace-function NAME]
                             script.py [script arguments...]
```

Use the Python that runs your program. The profiler runs your script in the same process, as `python script.py` does.

## Options

| Option | Default | Function |
|---|---|---|
| `--out FILE` | `.pmg/profile.json` | The profile file to write. A relative path starts from the current folder when the profiler starts. The profiler makes the folder if necessary. |
| `--root DIR` | the folder of the script | Only files below this folder are "your code". Time and memory go to lines in these files. Files in `site-packages` and in the standard library are never your code. The editor uses the workspace folder. |
| `--interval SECONDS` | `0.01` | The time between two samples. It must be more than zero. |
| `--memory fast` | yes | Time, and the growth of process memory (RSS) per line. Low cost. |
| `--memory precise` | | Time, and Python allocations with `tracemalloc`: memory per line, memory held at the peak and at the end, leaks and their holders, memory call stacks. High cost on code with many allocations. |
| `--memory off` | | Time only. The profile still records the RSS of the process. |
| `--frames N` | `2` | The traceback depth that `tracemalloc` records in precise mode, from 1 to 64. A larger value connects more library memory to your lines, but it is slower. |
| `--monitoring lines` | `off` | Records which lines of your code ran, with `sys.monitoring`. Python 3.12 or later. The time values still come from samples. |
| `--trace-function NAME` | | Precise mode only while functions with this name, or this qualified name (`Class.method`), run. All other code runs at full speed. It needs `--memory precise` and Python 3.12 or later. There is no leak detection and no native memory estimate in this mode. |

The profiler stops with an error (exit code 2) if:

- There is no script.
- `--interval` is zero, negative or infinite.
- `--frames` is less than 1 or more than 64.
- `--trace-function` has no name, the memory mode is not `precise`, or Python is older than 3.12.

## Script arguments

All arguments after the script go to your script without change. Your script sees them in `sys.argv`, and `sys.argv[0]` is the full path of the script. If the first argument after the options is `--`, the profiler removes it. Use `--` when your script name starts with `-`.

```bash
python server/pmg_profile.py --memory precise app.py --records 20000 --mode both
python server/pmg_profile.py --memory fast -- -script.py --verbose
```

The profile records the script arguments in `run.argv`. The Compare tab uses them to warn you when two runs have different arguments.

## When the profile is written

The profiler writes the profile when your script stops:

| How the script stops | Profile | Exit code of the profiler |
|---|---|---|
| The script ends normally | Written | 0 |
| `sys.exit(n)` | Written | `n` (1 if the value is not a number; 0 for `None`) |
| An exception that the script does not catch | Written. The traceback shows in the terminal. | 1 |
| Ctrl+C (SIGINT) | Written with the data up to that time | 130 |
| SIGTERM, for example `docker stop` | **Not written** | The process stops |
| SIGKILL, or the system stops the process because it has no memory | **Not written** | The process stops |

If threads that are not daemon threads still run when the script ends, the profiler waits for normal Python thread shutdown. Then it writes the profile.

The profiler writes the profile to a temporary file first, then renames it. Thus a reader never sees a half-written file. At the end, the profiler shows this line on standard error:

```
[pmg] profile written to /path/to/.pmg/profile.json
```

The profile format is in [profile-format.md](profile-format.md).

## Examples

Measure time and process memory:

```bash
python server/pmg_profile.py app.py
```

Find which code keeps memory, with a smaller input:

```bash
python server/pmg_profile.py --memory precise app.py --records 20000
```

Trace Python allocations only in one function:

```bash
python server/pmg_profile.py --memory precise --trace-function process_eager app.py
```

Connect more library memory to your lines (slower):

```bash
python server/pmg_profile.py --memory precise --frames 6 app.py
```

To show the result in VS Code, open the profile file and click the graph icon in the editor title bar. You can also run **Python Memory Guardian: Open Saved Profile Report**.

## In a container

In container mode, the extension copies `pmg_profile.py` to `.pmg/pmg_profile.py` in your project and runs it with `pythonMemoryGuardian.container.execPrefix`. To do the same by hand:

```bash
docker compose exec -T app python /app/.pmg/pmg_profile.py --out /app/.pmg/profile.json /app/src/service.py
```

The paths in this profile are container paths. The extension changes them to host paths when it loads the profile. See [container and remote setup](../03-container-setup.md).

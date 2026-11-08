# Python Memory Guardian

Python Memory Guardian (PMG) is a VS Code extension that finds Python code that uses too much memory or time. It reads your code while you type, and it measures your program when it runs. It shows results on source lines and in a report. The default profiler uses the Python standard library. Optional Memray capture adds allocator events.

<!-- SCREENSHOT: the Memory Guardian Report, Overview tab with the memory chart and the Phases table, next to a source file with inline labels. -->

## Key capabilities

- **Warnings while you type.** 22 rules find memory and concurrency patterns that cost more than they appear to. Examples are a full table in memory at one time, a new object per row, a list made only for one loop, a cache that only grows, and a call that blocks in `async def`. Each warning names the function and the variable, and gives a better pattern. The numbers in the warnings come from your own Python interpreter.
- **Time for each line.** A sampler measures each line and divides its time into Python time, native time (C code) and wait time.
- **Memory evidence.** Fast mode observes process RSS. Precise snapshots show live traced block bytes and allocation source paths. Sampled growth stays at process scope; it cannot identify an allocating line or thread.
- **Retention and GC.** PMG identifies recent sustained-growth candidates and bounded owner paths. GC callbacks measure collection frequency, pauses, collected objects and uncollectable objects. A cycle alone is not a leak.
- **Explicit diagnostic evidence.** Workload, cleanup, copy, structure and shared-access annotations can reproduce problems. Confirmed logical lost updates require conflicting access events and an incorrect observed outcome. Unannotated access remains unavailable.
- **Phases.** The report divides the run into phases (for example ingest, process, verify) and shows the new memory that each phase needed.
- **Comparison of two runs.** Save a run as a baseline, change your code, and compare. PMG marks a change as better or worse only when it is larger than the normal difference between two equal runs.
- **Results that tell their limits.** Each value states how PMG measured it and what it cannot show. A summary file, `.pmg/summary.json`, gives the same data to scripts and AI agents.
- **One function at a time.** Precise mode can trace only one function, so the rest of the program runs at full speed.

## PMG can help you

- Find which lines use the most time, and if the time is in Python, in C code, or in waits.
- Find source paths for live traced memory, and bounded paths to its owners.
- Investigate unwanted retention with repeated workloads and declared cleanup points.
- Find out if a change to the code decreased the time or the memory.
- See which static warnings have a cost in your real program.

> **Note:** PMG works on Linux, macOS and Windows, and in Dev Containers, Codespaces, Docker, WSL and Remote-SSH. It supports CPython 3.9 to 3.14. See the [limits](docs/07-technical-design.md#10-limits).

## Get started

You need VS Code 1.82 or later and Python 3.9 or later. Install the extension, open your project folder, and open a `.py` file: warnings show while you type. To measure your program, click the **pulse icon** in the editor title bar. The [quick start](docs/01-quickstart.md) gives the steps.

## Documentation

| Section | Pages |
|---|---|
| **Getting started** | [Quick start](docs/01-quickstart.md) |
| **How to use PMG** | [Using the extension](docs/02-using-the-extension.md): the report, labels, Compare tab, run summary, settings · [Container and remote setup](docs/03-container-setup.md) |
| **Concepts** | [Technical description](docs/07-technical-design.md): how PMG measures time and memory, its cost, and its limits |
| **Reference** | [Rules](docs/06-rules.md) · [Profiler command line](docs/reference/cli.md) · [Profile format](docs/reference/profile-format.md) · [Summary schema](docs/pmg-summary.schema.json) |
| **Project information** | [Changelog](CHANGELOG.md) · [Developer setup](docs/04-developer-setup.md) · [Build, install and release](docs/05-build-and-release.md) · [Feature map](docs/feature-map.md) · [License](LICENSE) |

[docs/00-guides.md](docs/00-guides.md) lists all pages.

## Development

```bash
npm ci
npm run vendor:python
npm test
```

See [developer setup](docs/04-developer-setup.md).

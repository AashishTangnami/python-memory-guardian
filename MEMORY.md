# Project memory

Use this file as a short orientation for future work. It is an index, not a second feature specification. The current source code is the authority for behavior; [docs/feature-map.md](docs/feature-map.md) is the canonical map of implemented features, execution flows, shared dependencies, code references, tests, and planned capabilities.

## Feature areas to account for

- **Static analysis:** Python and Rust language-server backends, diagnostic rules and advice, shared messages, interpreter facts, and backend selection.
- **Runtime profiling:** the VS Code profile command and standalone CLI, time and memory measurement, precise retention and holder evidence, and optional line monitoring.
- **Profile consumption:** profile schema validation, path mapping and source freshness, inline annotations, runtime warnings, and prioritization of static diagnostics.
- **Report and navigation:** profile Overview, memory diagnosis, the time-weighted Stack Explorer, direct visualization from JSON editor tabs, and navigation back to source.
- **Execution environments:** local and container profiling, helper staging, interpreter probing, path translation, and build-time resource packaging.

The map's **Planned, incomplete or unused capabilities** section is separate from implemented behavior. Do not treat a roadmap item as an available feature without confirming it in code.

## Keeping this memory useful

For a code change, inspect the affected source and the relevant part of the feature map. Follow [the repository documentation skill](.agents/skills/maintain-feature-docs/SKILL.md) to update the feature map or another relevant guide in the same change. Check related contracts when a change crosses the extension, Python server, Rust server, profiler, profile JSON, or container boundary. Keep this index brief; put implementation details in the feature map.

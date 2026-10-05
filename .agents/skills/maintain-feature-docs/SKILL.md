---
name: maintain-feature-docs
description: Keep this repository's feature map and relevant guides synchronized whenever application code, tests, scripts, build files, or code-related configuration changes.
---

# Maintain feature documentation

Apply this skill to every code-related change in this repository. Make a meaningful documentation edit in the same change, including for internal implementation updates. Choose the document that explains the changed behavior, contract, dependency, build step, or test workflow. Do not add filler text simply to satisfy this requirement.

## Documentation targets

- `docs/feature-map.md` is the canonical implemented-feature map. Update it when a change affects a trigger, command, editor event, setting, CLI action, view, processing path, result, failure path, shared dependency, process boundary, backend difference, schema, rule, or build-time resource relationship. Update its feature-to-code table and test references when the corresponding paths or symbols change. Keep planned and unused capabilities separate from implemented ones.
- Each topic has one home; update that file and link to it instead of repeating its content elsewhere (`docs/00-guides.md` lists them):
  - `docs/01-quickstart.md` through `docs/03-container-setup.md` for user-facing behavior, setup, settings, or container usage (`docs/02-using-the-extension.md` owns the report, labels, settings and user troubleshooting);
  - `docs/06-rules.md` for static rule codes, triggers and wording;
  - `docs/reference/cli.md` for profiler options, arguments and exit codes; `docs/reference/profile-format.md` for any `profile.json` field (with `docs/pmg-summary.schema.json` for `summary.json`);
  - `docs/07-technical-design.md` for how a measurement works, its accuracy, cost and limits (written in ASD-STE100 style: short sentences, active voice, no -ing verb forms);
  - `docs/04-developer-setup.md` for development, tests and debugging; `docs/05-build-and-release.md` for packaging, installation and publishing;
  - `README.md` only when the product summary, requirements, first-run steps or the documentation table change; it is also the Marketplace page.
- Update `CHANGELOG.md` when a user-visible release change warrants a release note.
- For internal changes that leave the feature overview intact, document the changed implementation relationship in the relevant dependency graph or developer guide. Do not alter the overview merely to record an internal refactor.

## Method

1. Inspect the code diff and follow affected calls and data contracts through the extension (`src`), Python server and profiler (`server`), Rust server (`rust-server/src`), manifest/build files, and tests as applicable. Use code as the authority; existing docs are cross-checks.
2. Before finishing, edit the relevant documentation in the same working tree. For feature-map changes, keep Mermaid diagrams compact and top-down, with at most five nodes horizontally; label every edge with the actual call, data flow, validation, render, publish, or build-time relationship. Distinguish runtime and data flow from build-time dependencies. Show process boundaries, important branches and failures, and Python/Rust alternatives where they affect behavior. Preserve the existing overview, colors, font, and line style unless the behavior or user request calls for changing them. Do not invent a relationship or omit a verified dependency to simplify the drawing.
3. Keep links, paths, symbols, feature-to-code rows, and relevant test names accurate. Provide code references for changed implementation relationships. Mark inferred relationships explicitly. If a dependency is shared intentionally, describe it as sharing; call it harmful coupling only when code evidence shows a concrete coordination or failure risk.
4. Verify the affected documentation against the final code diff. Check local links and Mermaid syntax or rendering where tools are available, and report any verification limit. Do not report a feature as implemented solely because it appears in a plan or old documentation.

Finish a code task only after its matching documentation change is complete. If a proposed edit has no documentable effect, explain that exception to the user instead of adding misleading text.

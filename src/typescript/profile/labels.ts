/** Editor and report text built from measurements: line labels, evidence and run notes. No `vscode` import. */
import { isGrowingRetention, memoryMb, pct, timeSplit } from './metrics';
import { FuncEntry, Heat, LineEntry, Profile } from './types';

const mb = (x: number) => (x >= 100 ? `${x.toFixed(0)} MB` : `${x.toFixed(1)} MB`);
const secs = (x: number) => (x >= 10 ? `${x.toFixed(1)} s` : `${x.toFixed(2)} s`);

/** Inline end-of-line label, e.g. "⏱ 0.61 s 8.8% (native 97%)  ▲ 160 MB". */
export function lineLabel(e: LineEntry, p: Profile): string {
  const out: string[] = [];
  if (e.time_s > 0) {
    const split = p.gil_split ? timeSplit(e) : "";
    const skew = p.memory_mode === "precise" ? " ~" : "";   // tracing inflates timings
    out.push(`⏱ ${secs(e.time_s)}${skew} ${pct(e.share)}${split ? ` (${split})` : ""}`);
  }
  const into = e.assigns?.length ? ` → ${e.assigns.join(", ")}` : "";
  if (p.memory_mode === "precise") {
    const mem: string[] = [];
    if ((e.peak_mb ?? 0) >= 1) mem.push(`held ${mb(e.peak_mb!)}`);
    if (mem.length) out.push(`▲ ${mem.join(" · ")}${into}`);
  }
  if (isGrowingRetention(e, p)) {
    const who = e.held_by?.length ? ` by ${e.held_by.map((h) => h.holder).join(", ")}` : "";
    out.push(`⚠ suspected growing retention: ${mb(e.end_mb ?? 0)} held${who}`);
  }
  return out.join("   ");
}

export function funcLabel(f: FuncEntry, p: Profile): string {
  const out = [`Σ ${f.name}(): ${secs(f.time_s)}`];
  const split = p.gil_split ? timeSplit(f) : "";
  if (split) out.push(`(${split})`);
  const m = p.memory_mode === "precise" ? f.peak_mb : 0;
  if (m >= 1) out.push(`· ${mb(m)} held at peak`);
  return out.join(" ");
}

/** Prefix for a static diagnostic, given measured data. */
export function evidence(e: LineEntry | undefined, h: Heat, p: Profile): string {
  if (h === "hot" && e) {
    const bits = [`${pct(e.share)} of runtime`];
    const m = memoryMb(e, p.memory_mode, p);
    if (m >= 1) bits.push(mb(m));
    if (isGrowingRetention(e, p)) bits.push("suspected growing retention");
    return `🔥 Measured in last profile: ${bits.join(", ")}. `;
  }
  if (h === "cold") return "❄ No runtime samples recorded here; execution is unconfirmed. ";
  return "";
}

/** "Service.handle() › `self.history.append` — " for runtime diagnostics (same style as static ones). */
export function runtimePrefix(e: LineEntry): string {
  const subject = e.calls?.[0] ?? e.assigns?.[0];
  const parts = [e.scope, subject ? `\`${subject}\`` : undefined].filter(Boolean);
  return parts.length ? `${parts.join(" › ")} — ` : "";
}

/** Status-bar tooltip note when much of the held memory couldn't be tied to a line. */
export function unattributedNote(p: Profile): string {
  const u = p.unattributed_peak_mb ?? 0;
  if (p.memory_mode !== "precise" || u < 1) return "";
  return `${mb(u)} held at the peak was allocated deep inside library code (e.g. an import) and ` +
    `couldn't be traced back to your lines with ${p.frames ?? 2}-frame tracebacks. Raise ` +
    `pythonMemoryGuardian.profile.frames to attribute more (slower).`;
}

/** Conditions that make part of a run's evidence incomplete; shown in the status tooltip and report. */
export function runNotes(p: Profile): string[] {
  const notes: string[] = [];
  if (p.memory_guard?.status === 'stopped')
    notes.push(`Memory guard stopped the run (${p.memory_guard.stop_reason}). The profile is incomplete; this is not a confirmed leak or OOM.`);
  if (p.memory_guard) notes.push(`Memory guard: ${(p.memory_guard.budget_bytes / 2 ** 20).toFixed(0)} MiB budget including in-process profiling; RSS polling can overshoot.`);
  if (p.memory_guard?.status === 'completed' && p.memory_guard.child_exit_code !== 0)
    notes.push(`The profiled process exited with code ${p.memory_guard.child_exit_code}; its cause is not inferred from the exit code.`);
  if (p.memory_tracing_lost_s != null) {
    notes.push(`The script stopped tracemalloc at ${secs(p.memory_tracing_lost_s)}; precise memory evidence ` +
      `covers only the run before that, and leak detection was skipped.`);
  }
  if (p.stack_sampling && !p.stack_sampling.active)
    notes.push(`Time per line, call stacks, function totals and phases were not sampled (${p.stack_sampling.reason ?? 'unavailable'}); per-line memory, retention and allocation stacks are still measured.`);
  if (p.sampler_error) notes.push(`Sampling stopped early (${p.sampler_error}); later timing is missing.`);
  if (p.allocation_backend?.backend === 'memray' && p.allocation_backend.available)
    notes.push('Allocator capture adds runtime and memory overhead. Captured timings describe the instrumented program; capture-file analysis also adds profiler runtime.');
  if (p.diagnostics) notes.push('Cooperative diagnostics add observer overhead and cover only annotated observations.');
  return notes;
}

export function leakMessage(e: LineEntry): string {
  const holders = (e.held_by ?? []).map((h) =>
    `\`${h.holder}\` (${h.type}, ${h.items} item${h.items === 1 ? "" : "s"})`);
  const held = holders.length
    ? `is still referenced by ${holders.join(" and ")}`
    : "was still held (holder not found)";
  return `${runtimePrefix(e)}⚠️ Suspected memory leak: retained allocations grew across ${e.leak_runs} ` +
    `trailing snapshot increases, and ${mb(e.end_mb ?? 0)} ${held} when profiling ended. ` +
    (holders.length ? "Bound it, evict old entries, or stop storing per-call data there."
                    : "Check for containers that only grow (globals, caches without a bound).");
}

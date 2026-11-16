/** The profile JSON contract written by src/python/pmg_profile.py. No `vscode` import. */
import { AllocationBackend } from './allocation';
import { MemoryGuard } from './memoryGuard';

export interface LineEntry {
  time_s: number; share: number;
  python_s: number; native_s: number; system_s: number; cpu_unsplit_s: number;
  samples: number; rss_growth_mb: number; rss_release_mb: number;
  line_events?: number;
  alloc_mb?: number; transient_peak_mb?: number; peak_mb?: number; end_mb?: number;
  /** precise mode: growth of tracemalloc's own bookkeeping while this line ran (profiler memory inside rss_growth_mb). */
  profiler_mb?: number;
  leak_runs?: number; func_line?: number;
  /** Names from the source line (via ast): enclosing scope, assigned variables, called functions. */
  scope?: string; assigns?: string[]; calls?: string[];
  /** Possible owners of retained allocations at the final ownership scan. */
  held_by?: { holder: string; type: string; items: number; matching: number }[];
  retention?: { snapshots: number; rises: number; releases: number; growth_mb: number;
    observed_s: number; peak_mb?: number; points: [number, number][] };
}

export interface FuncEntry {
  name: string; time_s: number; python_s: number; native_s: number; system_s: number;
  peak_mb: number; transient_peak_mb?: number; alloc_mb?: number; rss_growth_mb?: number; profiler_mb?: number;
  end_line?: number | null;
}

export interface StackFrame { file: string; line: number; name: string; first_line: number; user: boolean; }
export interface StackSample { thread: string; thread_name: string; frames: number[];
  python_s: number; native_s: number; system_s: number; unsplit_s: number; samples: number;
  /** precise mode: traced growth seen while this was the busiest thread's stack (sampled, net between samples). */
  alloc_bytes?: number; }

export interface Profile {
  schema: number; script: string; python: string; gil_split: boolean;
  wall_s: number; cpu_s?: number; memory_mode: "fast" | "precise" | "off";
  samples?: number; interval_s?: number; snapshots?: number;
  rss_kind?: "current" | "peak" | null;
  rss_start_mb?: number | null; rss_end_mb?: number | null;
  timeline?: [number, number, number][]; // elapsed seconds, traced MB, process RSS MB
  peak_traced_mb: number | null; rss_peak_mb: number | null; native_untraced_mb: number | null;
  /** precise mode: MB held at the peak whose tracebacks never reached the user's code. */
  unattributed_peak_mb?: number | null; frames?: number | null;
  /** precise mode: elapsed seconds at which the script stopped tracemalloc; memory evidence ends there. */
  memory_tracing_lost_s?: number | null;
  /** The sampler stopped early with this error; timing after it is missing. */
  sampler_error?: string | null;
  /** Absent in older profiles, which always sampled stacks. */
  stack_sampling?: { active: boolean; reason: string | null };
  file_hashes: Record<string, string>;
  files: Record<string, Record<string, LineEntry>>;
  functions: Record<string, Record<string, FuncEntry>>;
  stacks?: { frames: StackFrame[]; samples: StackSample[]; dropped_s: number; depth_limited: boolean };
  /** precise mode: bytes per allocation traceback at the peak and exit snapshots, outermost frame first. */
  memory_stacks?: MemoryStacks | null;
  /** fast/precise: objects still held at exit by globals or your instances' attributes, sized by sys.getsizeof. */
  largest_objects?: { objects: LargestObject[]; complete: boolean; scope?: string; max_depth?: number; max_nodes?: number;
    visited?: number; truncated?: boolean; sizing?: string } | null;
  monitoring?: { requested: 'off' | 'lines'; active: boolean; reason: string | null;
    dropped_line_events: number };
  /** Per timeline point: index into stacks.samples of the main thread's stack then; -1 when it had none. */
  timeline_stacks?: number[];
  /** precise mode: largest tracemalloc bookkeeping seen (profiler memory, not the program's). */
  tracemalloc_peak_mb?: number | null;
  /** precise mode scoped to one function (--trace-function): its name, traced calls, traced seconds. */
  trace_function?: { name: string; calls: number; traced_s: number } | null;
  /** How the run was started: the script's own arguments, UTC start time, sys.platform. Used to compare runs. */
  run?: { argv: string[]; started_at: string | null; platform: string; command?: string[]; cwd?: string };
  process_id?: number;
  /** Source snapshots are allocation provenance; sampled growth is a process observation only. */
  memory_attribution?: 'process-observation';
  /** False for lost tracing and function-scoped snapshots. */
  memory_snapshot_at_exit?: boolean;
  memory_observations?: MemoryObservation[];
  native_memory?: { status: 'unavailable'; reason: string; rss_residual_mb: number | null };
  gc?: GCMetrics;
  diagnostics?: Diagnostics;
  allocation_backend?: AllocationBackend;
  memory_guard?: MemoryGuard;
}

export interface MemoryObservation {
  t: number; traced_bytes: number | null; traced_growth_bytes: number | null;
  transient_bytes: number | null; rss_bytes: number | null; rss_change_bytes: number | null;
  bookkeeping_bytes: number | null;
}
export interface GCMetrics {
  supported: boolean;
  events: { t: number; duration_s: number; generation: number; collected: number; uncollectable: number; thread: string | number;
    process?: number; traced_bytes?: number | null }[];
  dropped_events: number; total_collections: number; total_pause_s: number; collected: number; uncollectable: number;
}
export interface EvidenceSource { file: string; line: number; function: string; }
export interface DiagnosticEvent {
  id: string | number; t: number; pid: number; thread: string | number; task: string | number | null;
  actor_kind: string; actor_id: string; source: EvidenceSource | null; stack: EvidenceSource[];
  category: string; payload: Record<string, unknown>;
}
export interface RuntimeFinding {
  id: string; category: string; severity: 'error' | 'warning' | 'information' | 'hint';
  confidence: string; status: 'measured' | 'confirmed' | 'potential' | 'projected';
  source: EvidenceSource | null; workload: unknown; evidence: Record<string, unknown>;
  reproduction: { command: string | string[] | null; trace_path: string | null }; summary: string;
  pid?: number; thread?: string | number; actor_kind?: string; actor_id?: string;
}
export interface Diagnostics {
  schema: 1; instrumentation: 'cooperative'; trace_path: string | null;
  trace_sha256?: string | null;
  events: DiagnosticEvent[]; findings: RuntimeFinding[]; dropped_events: number;
  scan_limits: { max_nodes: number; max_depth: number };
  capabilities: Record<string, { status: 'supported' | 'partial' | 'unavailable'; reason: string }>;
}

export interface MemoryStackTable {
  t: number; total_bytes: number; other_bytes: number;
  stacks: { frames: number[]; bytes: number; truncated: boolean }[];
}
export interface MemoryStacks { depth: number; frames: StackFrame[]; peak: MemoryStackTable | null; exit: MemoryStackTable; }

export interface LargestObject { holder: string; type: string; mb: number; items: number | null; estimated: boolean; }

export interface Thresholds { hotShare: number; hotMb: number; }

export type Heat = "hot" | "cold" | "unknown";

/** The run facts that decide whether memory evidence is complete. */
export type NativeRun = Pick<Profile, 'memory_mode'> & { tracemalloc_peak_mb?: number | null; native_untraced_mb?: number | null;
  trace_function?: { name: string } | null; memory_tracing_lost_s?: number | null; memory_snapshot_at_exit?: boolean;
  stack_sampling?: { active: boolean } };

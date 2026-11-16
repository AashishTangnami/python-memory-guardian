/** Native report data: the profile overview. */
import { isGrowingRetention, processResidualMb } from '../../profile/metrics';
import { Profile } from '../../profile/types';

export interface OverviewLine {
  file: string; line: number; timeS: number; share: number; memory: string;
}

export interface ReportOverview {
  cpuS: number | null; samples: number | null;
  /** Legacy name: positive RSS residual. It is uncertain and has no native allocation provenance. */
  nativeUntracedMb: number | null;
  rssKind: 'current' | 'peak' | null;
  rssStartMb: number | null; rssEndMb: number | null; rssPeakMb: number | null;
  tracedPeakMb: number | null; timeline: [number, number, number][];
  topLines: OverviewLine[];
}

/** Keep webview payloads bounded; every value comes from the validated profile. */
export function overview(profile: Profile): ReportOverview {
  const topLines: OverviewLine[] = [];
  for (const [file, lines] of Object.entries(profile.files)) {
    for (const [line, e] of Object.entries(lines)) {
      if (e.time_s <= 0) continue;
      let memory = '';
      if (profile.memory_mode === 'precise') {
        if (isGrowingRetention(e, profile)) memory = `${(e.end_mb ?? 0).toFixed(1)} MB held`;
        else if ((e.peak_mb ?? 0) >= 1) memory = `${e.peak_mb!.toFixed(1)} MB at peak`;
      }
      topLines.push({ file, line: Number(line), timeS: e.time_s, share: e.share, memory });
    }
  }
  topLines.sort((a, b) => b.timeS - a.timeS || a.file.localeCompare(b.file) || a.line - b.line);
  const points = profile.timeline ?? [];
  const stride = Math.max(1, Math.ceil(points.length / 299));
  return {
    cpuS: profile.cpu_s ?? null, samples: profile.samples ?? null, rssKind: profile.rss_kind ?? null,
    // Not when only one function was traced: untraced Python memory would read as native.
    nativeUntracedMb: processResidualMb(profile),
    rssStartMb: profile.rss_start_mb ?? null, rssEndMb: profile.rss_end_mb ?? null,
    rssPeakMb: profile.rss_peak_mb ?? null, tracedPeakMb: profile.peak_traced_mb ?? null,
    timeline: points.filter((_, i) => i % stride === 0 || i === points.length - 1),
    topLines: topLines.slice(0, 10),
  };
}

/**
 * Options for "Profile Current File" that need no `vscode` import, so they are unit-testable with plain Node:
 * splitting a typed argument string into argv, and naming the function around the cursor.
 */

/**
 * Split script arguments as a POSIX shell would for plain words: whitespace separates, single quotes keep
 * everything literally, double quotes keep spaces and allow \" and \\, a backslash outside quotes escapes the
 * next character. No variables, globs or redirection: the profiler is started without a shell.
 * Returns an error message for an unterminated quote or a trailing backslash.
 */
export function splitArgs(text: string): string[] | { error: string } {
  const out: string[] = [];
  let cur = '', inWord = false, quote: '"' | "'" | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote === "'") {
      if (c === "'") quote = null; else cur += c;
    } else if (quote === '"') {
      if (c === '"') quote = null;
      else if (c === '\\' && (text[i + 1] === '"' || text[i + 1] === '\\')) cur += text[++i];
      else cur += c;
    } else if (c === "'" || c === '"') {
      quote = c; inWord = true;
    } else if (c === '\\') {
      if (i + 1 >= text.length) return { error: 'A trailing backslash escapes nothing.' };
      cur += text[++i]; inWord = true;
    } else if (/\s/.test(c)) {
      if (inWord) { out.push(cur); cur = ''; inWord = false; }
    } else {
      cur += c; inWord = true;
    }
  }
  if (quote) return { error: `Unterminated ${quote === '"' ? 'double' : 'single'} quote.` };
  if (inWord) out.push(cur);
  return out;
}

/**
 * The name of the innermost `def` enclosing a 0-based line: the nearest `def` at or above it whose indentation
 * is smaller than the line's (or the `def` line itself). Blank and comment lines are skipped. A line at module
 * level, or below the end of a function's indented body, has none. The profiler matches this name against
 * function names and qualified names, so a method name also matches same-named methods in other classes.
 */
export function enclosingFunction(lines: string[], line: number): string | undefined {
  const indent = (s: string) => s.length - s.trimStart().length;
  const code = (s: string) => s.trim() !== '' && !s.trimStart().startsWith('#');
  let at = Math.min(line, lines.length - 1);
  while (at >= 0 && !code(lines[at])) at--;
  if (at < 0) return undefined;
  const def = /^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(/;
  const here = def.exec(lines[at]);
  if (here) return here[1];
  let limit = indent(lines[at]);
  for (let i = at - 1; i >= 0 && limit > 0; i--) {
    if (!code(lines[i])) continue;
    const d = indent(lines[i]);
    if (d >= limit) continue;
    const m = def.exec(lines[i]);
    if (m) return m[1];
    limit = d;                       // a class, if, for or with: keep looking for a def further out
  }
  return undefined;
}

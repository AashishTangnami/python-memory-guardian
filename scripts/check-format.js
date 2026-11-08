/** Check repository layout without reflowing its compact source style. */
const fs = require('fs');
const path = require('path');
const roots = ['src/typescript', 'src/python', 'scripts', 'src/rust/src', '.github/workflows'];
const extensions = new Set(['.ts', '.js', '.py', '.rs', '.json', '.yml', '.yaml', '.toml']);
let failures = 0;

function check(file) {
  const text = fs.readFileSync(file, 'utf8');
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  if (text && !text.endsWith('\n')) {
    console.error(`${file}: missing final newline`);
    failures++;
  }
  for (let index = 0; index < lines.length; index++) {
    if (/[\t ]+$/.test(lines[index]) || /^ *\t/.test(lines[index])) {
      console.error(`${file}:${index + 1}: trailing whitespace or tab indentation`);
      failures++;
    }
  }
  if (path.extname(file) === '.json') {
    try {
      JSON.parse(text);
    } catch (error) {
      console.error(`${file}: ${error.message}`);
      failures++;
    }
  }
}

function walk(directory) {
  if (!fs.existsSync(directory)) return;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (['libs', '__pycache__', 'node_modules'].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (entry.isFile() && extensions.has(path.extname(file))) check(file);
  }
}

for (const root of roots) walk(root);
for (const file of ['package.json', 'eslint.config.mjs', 'pyproject.toml', 'docs/pmg-summary.schema.json']) check(file);
process.exitCode = failures ? 1 : 0;
if (!failures) console.log('PASS source layout, final newlines, whitespace and JSON syntax');

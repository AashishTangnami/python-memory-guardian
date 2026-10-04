// Profile Current File options: script argument splitting and the function around the cursor.
const assert = require('assert');
const { splitArgs, enclosingFunction } = require('../out/runOptions');

assert.deepStrictEqual(splitArgs('--records 20000  --mode both'), ['--records', '20000', '--mode', 'both']);
assert.deepStrictEqual(splitArgs(`--name "a b" 'c "d"' e\\ f "x\\"y"`), ['--name', 'a b', 'c "d"', 'e f', 'x"y'],
  'double and single quotes, escaped space, escaped quote');
assert.deepStrictEqual(splitArgs(''), []);
assert.deepStrictEqual(splitArgs('""'), [''], 'an explicit empty argument is kept');
assert(splitArgs('"open').error, 'unterminated quote is an error');
assert(splitArgs('end\\').error, 'trailing backslash is an error');

const src = ['import os', '', 'class A:', '    def run(self, x):', '        y = x', '', '        # note', '        return y', '',
  'def top():', '    if True:', '        z = 1', '    return z', '', 'after = 1'];
assert.strictEqual(enclosingFunction(src, 4), 'run');
assert.strictEqual(enclosingFunction(src, 6), 'run', 'a comment line inside the body');
assert.strictEqual(enclosingFunction(src, 3), 'run', 'the def line itself');
assert.strictEqual(enclosingFunction(src, 11), 'top', 'inside an if block');
assert.strictEqual(enclosingFunction(src, 2), undefined, 'a class line is not a function');
assert.strictEqual(enclosingFunction(src, 14), undefined, 'module level after the function');
assert.strictEqual(enclosingFunction(src, 0), undefined);
assert.strictEqual(enclosingFunction(['async def fetch(url):', '    return 1'], 1), 'fetch', 'async def');
console.log('PASS run options: argument splitting and the enclosing function');

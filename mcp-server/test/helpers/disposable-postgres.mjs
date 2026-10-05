import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

// Use the same kernel-owned lease as Python proofs, across all worktrees.
export async function acquirePostgresFixtureGroup() {
  const child = spawn('python3', ['-u', fileURLToPath(new URL('../../../lib/disposable_pg_fixture.py', import.meta.url))],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  const lines = createInterface({ input: child.stdout });
  let diagnostic = '';
  child.stderr.on('data', data => { diagnostic = (diagnostic + data).slice(-2000); });
  const completed = new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  try {
    await Promise.race([
      new Promise((resolve, reject) => {
        child.once('error', reject);
        lines.once('line', line => line === 'ready' ? resolve() : reject(new Error('invalid PostgreSQL fixture lease response')));
      }),
      completed.then(() => { throw new Error(`PostgreSQL fixture lease failed: ${diagnostic}`); }),
    ]);
  } catch (error) {
    child.stdin.end();
    await completed;
    throw error;
  } finally {
    lines.close();
  }
  let released;
  return () => released ??= (async () => {
    child.stdin.end();
    const { code, signal } = await completed;
    if (code !== 0) throw new Error(`PostgreSQL fixture lease exited (${signal ?? code}): ${diagnostic}`);
  })();
}

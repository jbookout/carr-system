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

export async function withPostgresFixture({ tables = [], setup = '' }, run) {
  const [{ execFileSync }, fs, path, { default: pg }] = await Promise.all([
    import('node:child_process'), import('node:fs'), import('node:path'), import('pg'),
  ]);
  const candidates = ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config',
    '/usr/lib/postgresql/17/bin/pg_config', '/usr/lib/postgresql/16/bin/pg_config'];
  let bin;
  for (const candidate of candidates) {
    try {
      const found = execFileSync(candidate, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
      if (['initdb', 'pg_ctl', 'postgres'].every(name => fs.existsSync(path.join(found, name)))) { bin = found; break; }
    } catch { /* Try the next installed PostgreSQL distribution. */ }
  }
  if (!bin) throw new Error('disposable verb fixture requires local PostgreSQL binaries');
  const release = await acquirePostgresFixtureGroup();
  const dir = fs.mkdtempSync('/tmp/carr-verb-pg-');
  const clients = [];
  let attempted = false;
  const transaction = async (client, operation) => {
    await client.query('begin');
    try { const result = await operation(client); await client.query('commit'); return result; }
    catch (error) { await client.query('rollback'); throw error; }
  };
  try {
    execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--encoding=UTF8', '--no-locale'], { stdio: 'pipe' });
    attempted = true;
    execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h '' -c timezone=UTC`, '-w', 'start'], { stdio: 'pipe' });
    const connect = async () => {
      const client = new pg.Client({ host: dir, user: 'fixture', database: 'postgres' });
      await client.connect(); clients.push(client); return client;
    };
    const fixture = { connect, transaction, command: async (client, actor, name, args) => {
      const { executeRegisteredTool } = await import('../../src/tools.js');
      return transaction(client, c => executeRegisteredTool(c, actor, name, args));
    } };
    const c = await connect();
    if (tables.length) {
      const schema = fs.readFileSync(new URL('../../../db/schema.sql', import.meta.url), 'utf8');
      for (const name of tables) {
        if (!/^(?:public|ops)\.[a-z_]+$/.test(name)) throw new Error(`invalid fixture table: ${name}`);
        const sql = schema.match(new RegExp(`CREATE TABLE ${name.replaceAll('.', '\\.')} \\([\\s\\S]*?\\n\\);`))?.[0];
        if (!sql) throw new Error(`canonical fixture table missing: ${name}`);
        await c.query(sql);
      }
    }
    if (setup) await c.query(setup);
    await run({ ...fixture, c });
  } finally {
    try {
      await Promise.all(clients.map(client => client.end()));
      if (attempted) execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-m', 'fast', '-w', 'stop'], { stdio: 'pipe' });
    } finally {
      await release();
      fs.mkdirSync('/tmp/_to_delete', { recursive: true });
      fs.renameSync(dir, path.join('/tmp/_to_delete', path.basename(dir)));
    }
  }
}

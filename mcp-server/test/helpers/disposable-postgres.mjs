import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const supervisors = new Set();
const endSupervisors = () => { for (const child of supervisors) child.stdin.end(); };
const interrupt = () => process.exit(130);
const terminate = () => process.exit(143);
function register(child) {
  if (supervisors.size === 0) {
    process.on('exit', endSupervisors);
    process.on('SIGINT', interrupt);
    process.on('SIGTERM', terminate);
  }
  supervisors.add(child);
}
function unregister(child) {
  supervisors.delete(child);
  if (supervisors.size === 0) {
    process.off('exit', endSupervisors);
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
  }
}

async function supervisor(args, response) {
  const child = spawn('python3', ['-u', fileURLToPath(new URL('../../../lib/disposable_pg_fixture.py', import.meta.url)), ...args],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  register(child);
  // An early supervisor failure can close stdin before teardown writes to it.
  child.stdin.on('error', () => {});
  const lines = createInterface({ input: child.stdout });
  let diagnostic = '';
  child.stderr.on('data', data => { diagnostic = (diagnostic + data).slice(-4000); });
  const completed = new Promise(resolve => {
    child.once('error', error => resolve({ error }));
    child.once('close', (code, signal) => resolve({ code, signal }));
  });
  let value;
  try {
    value = await Promise.race([
      new Promise((resolve, reject) => {
        lines.once('line', line => {
          try { resolve(response(line)); } catch (error) { reject(error); }
        });
      }),
      completed.then(result => { throw result.error ?? new Error(`PostgreSQL fixture supervisor failed: ${diagnostic}`); }),
    ]);
  } catch (error) {
    child.stdin.end();
    await completed;
    unregister(child);
    throw error;
  } finally {
    if (!args.includes('--supervise')) lines.close();
  }
  let closed;
  const run = async (command, args = []) => {
    if (closed) throw new Error('PostgreSQL fixture is closed');
    const reply = new Promise((resolve, reject) => {
      const receive = line => {
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      };
      lines.once('line', receive);
      completed.then(result => {
        lines.off('line', receive);
        reject(result.error ?? new Error(`PostgreSQL fixture supervisor exited during command: ${diagnostic}`));
      });
    });
    child.stdin.write(JSON.stringify({ command: [command, ...args] }) + '\n');
    const result = await reply;
    if (result.returncode !== 0) throw new Error(`PostgreSQL fixture command failed (${result.returncode}): ${result.stderr}`);
    return result.stdout;
  };
  return { value, run, close: () => closed ??= (async () => {
    child.stdin.end();
    const { code, signal, error } = await completed;
    unregister(child);
    lines.close();
    if (error) throw error;
    if (code !== 0) throw new Error(`PostgreSQL fixture supervisor exited (${signal ?? code}): ${diagnostic}`);
  })() };
}

// Use the same kernel-owned lease as Python proofs, across all worktrees.
export async function acquirePostgresFixtureGroup() {
  const lease = await supervisor([], line => {
    if (line !== 'ready') throw new Error('invalid PostgreSQL fixture lease response');
  });
  return lease.close;
}

// The supervisor owns the cluster before initdb/start and survives abrupt Node exit.
export async function acquireDisposablePostgres({ prefix, pgCtl, dataName = 'data' }) {
  const fixture = await supervisor(['--supervise', '--prefix', prefix, '--pg-ctl', pgCtl, '--data-name', dataName], line => {
    if (!line.startsWith('/')) throw new Error('invalid PostgreSQL fixture root response');
    return line;
  });
  return { root: fixture.value, close: fixture.close, run: fixture.run };
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
  const clients = [];
  let cluster;
  const transaction = async (client, operation) => {
    await client.query('begin');
    try { const result = await operation(client); await client.query('commit'); return result; }
    catch (error) { await client.query('rollback'); throw error; }
  };
  try {
    cluster = await acquireDisposablePostgres({ prefix: 'carr-verb-pg-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    const dir = cluster.root;
    await cluster.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--encoding=UTF8', '--no-locale']);
    await cluster.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h '' -c timezone=UTC`, '-w', 'start']);
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
    } finally {
      try { await cluster?.close(); } finally { await release(); }
    }
  }
}

// Rebuild pending seals through the chain's SQL and runtime renderers.
import {execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [repo, base, mapping, mode] = process.argv.slice(2);
const preview = mode === '--preflight' ? JSON.parse(readFileSync(0, 'utf8')) : null;
const renames = JSON.parse(mapping);
process.chdir(repo);
const load = path => JSON.parse(readFileSync(path, 'utf8'));
const main = path => JSON.parse(execFileSync('git', ['show', `${base}:${path}`],
  {encoding:'utf8', maxBuffer:16 * 1024 * 1024, timeout:60000}));
const chainPath = 'ops/config/scac-registry-chain.json';
const fixturePath = 'ops/config/scac-registry-source-inventory-fixtures.v1.json';
const sealPath = 'ops/config/scac-registry-full-entry-set-seals.json';
let chain = main(chainPath);
const branch = preview?.chain ?? load(chainPath);
const moduleAt = path => import(pathToFileURL(resolve(repo, path)).href);
const {appendSuccessor, preservesRegistryChainHistory} = await moduleAt('ops/registry-chain.mjs');
if (!preservesRegistryChainHistory(chain, branch))
  throw new Error('applied registry history differs from main; rebase its successor first');
const pending = branch.versions.slice(chain.versions.length);
if (!pending.length) throw new Error('no pending registry successor owns these generated outputs');
const {historicalRows} = await moduleAt('ops/registry-history.mjs');
const rows = pending.map(row => preview ? historicalRows(row.number, preview.fixture) : historicalRows(row.number));
if (preview) {
  for (const contracts of rows) for (const row of contracts) {
    const proposed = preview.source_digests[row.source_locator];
    if (Object.hasOwn(preview.source_digests, row.source_locator) && proposed !== row.source_digest)
      throw new Error(`renumbering changes source contract ${row.source_locator}; refresh it and its dependent seals through the owning generation path first`);
  }
  process.exit(0);
}
const {writeIntegratedArtifact} = await moduleAt('ops/integration-generation.mjs');
const allocated = name => renames[name] || name;
const originalFixture = readFileSync(fixturePath);
const originalChain = readFileSync(chainPath);
try {
  writeFileSync(fixturePath, JSON.stringify(main(fixturePath), null, 2)+'\n');
  for (const [index, row] of pending.entries()) {
    if (row.number !== chain.versions.at(-1).number + 1)
      throw new Error('pending registry must follow the current-main predecessor');
    const successor = allocated(row.migration.split('/').at(-1));
    const domains = row.atomic_pair.filter(name => name !== row.migration.split('/').at(-1));
    if (!domains.length) throw new Error('pending registry lacks its domain migration group');
    const result = appendSuccessor({chain, rows:rows[index], catalog:row.catalog, entrySetDigest:row.entry_set_digest,
      domainMigration:domains.map((name, i) => ({filename:allocated(name),
        sql:readFileSync('migrations/'+allocated(name), 'utf8'),
        ...(i === domains.length-1 ? {successor_filename:successor} : {})}))});
    await writeIntegratedArtifact('migrations/'+successor, result.sql);
    await writeIntegratedArtifact('mcp-server/src/scac-mutation-registry.current.generated.js', result.runtime);
    chain = result.chain;
    writeFileSync(chainPath, JSON.stringify(chain, null, 2)+'\n');
    writeFileSync(fixturePath, JSON.stringify(result.fixture, null, 2)+'\n');
    writeFileSync(sealPath, JSON.stringify(result.seals, null, 2)+'\n');
  }
} catch (error) {
  writeFileSync(fixturePath, originalFixture);
  writeFileSync(chainPath, originalChain);
  throw error;
}

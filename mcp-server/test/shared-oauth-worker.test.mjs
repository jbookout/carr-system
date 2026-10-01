import test from "node:test";
import assert from "node:assert/strict";
import { builtinModules } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

test("Worker entrypoint applies OAuth policy before authentication and alternate MCP doors", async () => {
  const repo = fileURLToPath(new URL("../../", import.meta.url));
  const bundle = await build({ absWorkingDir: repo, entryPoints: ["mcp-server/src/index.js"],
    bundle: true, write: false, format: "esm", platform: "neutral", logLevel: "silent",
    mainFields: ["browser", "module", "main"], conditions: ["workerd", "worker", "browser"],
    external: ["cloudflare:*", "node:*"], loader: { ".ttf": "binary" },
    banner: { js: 'import { createRequire } from "node:module"; const require = createRequire("/index.js");' },
    plugins: [{ name: "node-compat", setup(builder) {
      builder.onResolve({ filter: /^[a-z]/ }, args => builtinModules.includes(args.path)
        ? { path: "node:" + args.path, external: true } : undefined);
    } }],
  });
  // Explicit synthetic bindings; no wrangler config, local credential file,
  // Google endpoint or production database is read by this fixture.
  const worker = new Miniflare({ ...convertV4MiniflareOptions({ modules: true, script: bundle.outputFiles[0].text,
    compatibilityDate: "2026-09-01", compatibilityFlags: ["nodejs_compat", "global_fetch_strictly_public"],
    kvNamespaces: ["OAUTH_KV"], bindings: { GOOGLE_CLIENT_ID: "synthetic-config", GOOGLE_CLIENT_SECRET: "synthetic-config" },
  }), resourcePersistencePath: fileURLToPath(new URL("../../out/_to_delete/oauth-worker-fixture/", import.meta.url)),
    unsafeEnableSharedStorage: false });
  try {
    const origin = "https://oauth.example";
    for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes"]) {
      for (const method of ["GET", "POST", "OPTIONS"]) {
        const refused = await worker.dispatchFetch(origin + path, { method, headers: { origin: "null" } });
        assert.equal(refused.status, 403);
        assert.equal(refused.headers.get("access-control-allow-origin"), null);
      }
    }
    assert.equal((await worker.dispatchFetch(origin + "/healthz")).status, 200);
    for (const allowed of [null, "https://chatgpt.com", "https://claude.ai"]) {
      assert.equal((await worker.dispatchFetch(origin + "/mcp", { headers: allowed ? { origin: allowed } : {} })).status, 401);
    }
    assert.equal((await worker.dispatchFetch(origin + "/consent")).status, 405);
    assert.equal((await worker.dispatchFetch(origin + "/token", { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" }, body: "grant_type=authorization_code" })).status, 400);
  } finally { await worker.dispose(); }
});

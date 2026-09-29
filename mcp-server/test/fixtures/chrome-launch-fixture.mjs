import { createServer } from "node:http";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

const [profile, scenario] = process.argv.slice(2);
const portFile = path.join(profile, "DevToolsActivePort");
process.stderr.write(`fixture:${scenario}\n`);
if (scenario === "inherited-stderr") {
  const holder = spawn(process.execPath, ["-e", "setTimeout(() => {}, 4000)"], { detached: true, stdio: ["ignore", "ignore", "inherit"] });
  holder.unref();
}
if (scenario === "hang-ignore-term") process.on("SIGTERM", () => {});
if (scenario === "exit") process.exit(17);
if (scenario.startsWith("hang")) setInterval(() => {}, 1000);
else {
  let ready = scenario !== "http-late";
  const server = createServer((req, res) => {
    if (scenario === "http-hang") return;
    if (!ready) { res.writeHead(503).end(); return; }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify([{ type: "page", webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/devtools/page/fixture` }]));
  });
  server.listen(0, "127.0.0.1", async () => {
    const contents = `${server.address().port}\n/devtools/browser/fixture\n`;
    if (scenario === "partial") {
      await writeFile(portFile, "");
      setTimeout(() => writeFile(portFile, contents), 100);
    } else await writeFile(portFile, contents);
    if (scenario === "http-late") setTimeout(() => { ready = true; }, 100);
  });
}

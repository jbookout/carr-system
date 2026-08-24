import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { confidenceInfo, errorMessage, freshness, isDncStage, isTerminal, stageChoices } from "../js/leads-app.js";

const root = new URL("..", import.meta.url);
const read = (file) => readFile(new URL(file, root), "utf8");

test("lead board shell carries accessible controls, status regions, and pipeline SVG markers", async () => {
  const html = await read("leads.html");
  assert.match(html, /<a class="skip" href="#leadBoard">/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /<svg[^>]+role="img"[^>]+aria-labelledby=/);
  assert.match(html, /<marker id="pipelineArrow"/);
  assert.match(html, /class="pipeline-track"[^>]+tabindex="0"/);
  assert.match(html, /id="leadSearch"/);
  assert.match(html, /id="densityToggle"[^>]+aria-pressed=/);
  assert.match(html, /id="boardView"[^>]+aria-pressed="true"/);
  assert.match(html, /id="listView"[^>]+aria-pressed="false"/);
  assert.match(html, /id="refreshBoard"/);
});

test("lead board styling provides semantic motion states and disables all motion when reduced", async () => {
  const css = await read("css/leads.css");
  assert.match(css, /data-freshness="healthy"/);
  assert.match(css, /data-freshness="attention"/);
  assert.match(css, /data-freshness="overdue"/);
  assert.match(css, /data-freshness="terminal"/);
  assert.match(css, /3\.5s/);
  assert.match(css, /2s/);
  assert.match(css, /1s/);
  assert.match(css, /animation:pipeline-flow 8s linear infinite/);
  assert.match(css, /@keyframes pipeline-flow/);
  assert.match(css, /min-width:760px/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(css, /animation:\s*none\s*!important/);
  assert.match(css, /content-visibility:\s*auto/);
  assert.match(css, /contain-intrinsic-size:\s*auto\s+260px/);
});

test("lead app exposes accessible stage actions and conflict refresh behavior", async () => {
  const app = await read("js/leads-app.js");
  assert.match(app, /aria-label="Move /);
  assert.match(app, /document\.activeElement/);
  assert.match(app, /version_conflict/);
  assert.match(app, /await refresh\(\)/);
  assert.match(app, /await refresh\(\);\s*\$\("leadBoardError"\)\.textContent = message/);
  assert.match(app, /\$\("refreshBoard"\)\.addEventListener/);
  assert.match(app, /state\.view === "list"/);
  assert.match(app, /class="lead-list"/);
  assert.match(app, /isDncStage\(select\.value\)/);
  assert.match(app, /Stage locked by suppression instruction/);
  assert.doesNotMatch(app, /reminder|outreach|create-lead|promote-lead|decline-lead/i);
});

test("confidence and terminal helpers preserve production values and lock suppression instructions", () => {
  assert.deepEqual(confidenceInfo("high"), { text: "High confidence", verify: false });
  assert.deepEqual(confidenceInfo("medium"), { text: "Medium confidence", verify: true });
  assert.deepEqual(confidenceInfo("low"), { text: "Low confidence", verify: true });
  assert.deepEqual(confidenceInfo(null), { text: "Confidence missing", verify: true });
  assert.deepEqual(confidenceInfo(0.82), { text: "82% confidence", verify: false });
  assert.deepEqual(confidenceInfo(0.35), { text: "35% confidence", verify: true });
  assert.equal(isDncStage("do_not_contact"), true);
  assert.equal(isDncStage("contacted"), false);
  const stages = [{ slug: "new" }, { slug: "do_not_contact" }, { slug: "contacted" }];
  assert.deepEqual(stageChoices(stages, { stage: "new" }).map((stage) => stage.slug), ["new", "contacted"]);
  assert.deepEqual(stageChoices(stages, { stage: "do_not_contact" }).map((stage) => stage.slug), ["new", "do_not_contact", "contacted"]);
  assert.equal(isTerminal({ suppressed: true, stage: "new" }), true);
  assert.equal(isTerminal({ suppressed: false, stage: "do_not_contact" }), true);
  assert.equal(isTerminal({ do_not_contact: true, stage: "new" }), true);
  assert.equal(freshness({ suppressed: true, stage: "new" }).key, "terminal");
  assert.deepEqual(freshness({ suppressed: false, stage: "do_not_contact" }),
    { key: "terminal", text: "Do not contact" });
  assert.equal(freshness({ do_not_contact: true, stage: "new" }).key, "terminal");
  assert.match(errorMessage({ code: "unauthorized" }), /session has ended/i);
});

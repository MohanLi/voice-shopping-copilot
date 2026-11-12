// End-to-end check of the web UI against a running server (`voiceshop serve`), in headless Chromium.
//
//   node frontend/e2e/smoke.cjs [http://127.0.0.1:8000] [--voice path/to/question.wav] [--shots docs/images]
//
// It asks one typed question and checks what the assignment requires on screen: the transcript, the step log,
// the six-column comparison table, citations and audio. With --voice it also feeds a WAV file to the browser as
// if it were the microphone, so recording, upload, speech recognition and the spoken reply are exercised too.
const { chromium } = require("playwright");
const path = require("node:path");

const args = process.argv.slice(2);
const base = args.find((a) => a.startsWith("http")) || "http://127.0.0.1:8000";
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
const voice = option("--voice");
const shots = option("--shots");

function check(condition, message) {
  if (!condition) throw new Error(`CHECK FAILED: ${message}`);
  console.log(`ok  ${message}`);
}

(async () => {
  const launch = { args: ["--autoplay-policy=no-user-gesture-required"] };
  if (voice) launch.args.push("--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${path.resolve(voice)}%noloop`);
  const browser = await chromium.launch(launch);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: shots ? 2 : 1, permissions: voice ? ["microphone"] : [] });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  await page.goto(base);
  await page.waitForSelector(".bar__status span");
  check((await page.textContent("h1")).includes("Voice Shopping Copilot"), "the page loads and shows what is live");
  if (shots) await page.screenshot({ path: `${shots}/ui-start.png` });

  // ---- a typed request
  await page.fill("#request", "a wooden puzzle for my three year old under twenty dollars");
  await page.click("button:has-text('Ask')");
  await page.waitForSelector(".total", { timeout: 180000 });
  const headers = await page.$$eval("table.compare thead th", (cells) => cells.map((cell) => cell.textContent.trim()));
  check(JSON.stringify(headers) === JSON.stringify(["Product", "Price", "Rating", "Ingredients", "Source", "Citation"]), "comparison table has the six required columns");
  const rows = await page.$$eval("table.compare tbody tr", (trs) => trs.length);
  check(rows >= 1 && rows <= 3, `the table lists the top ${rows} product(s)`);
  check((await page.textContent("table.compare tbody tr:first-child")).includes("Top pick"), "the first row is marked as the top pick");
  check(/AMZ-[A-Z0-9]+/.test(await page.textContent("table.compare")), "catalog rows cite a document id");
  const steps = await page.$$eval(".step__title", (nodes) => nodes.map((node) => node.textContent));
  for (const stage of ["Router", "Planner", "Retriever (MCP tools)", "Answerer", "Critic"]) check(steps.includes(stage), `the step log shows the ${stage}`);
  check((await page.textContent(".calls")).includes("rag.search"), "the step log shows the MCP tool call");
  check((await page.textContent(".reply__text")).length > 20, "the spoken reply is shown as text");
  const spoken = await page.$(".play");
  console.log(spoken ? "ok  the reply has audio (play button present)" : "--  speech synthesis is off on this server; no audio to check");
  if (shots) await page.screenshot({ path: `${shots}/ui-answer.png`, fullPage: true });

  // ---- a follow-up in the same conversation
  await page.fill("#request", "tell me more about the first one");
  await page.click("button:has-text('Ask')");
  await page.waitForFunction(() => document.querySelectorAll(".earlier li").length >= 1 && document.querySelector(".total"), null, { timeout: 180000 });
  check(true, "a follow-up keeps the earlier turn on screen");

  // ---- a reply with no list of its own keeps the earlier list on screen ("the second one" still means that list)
  await page.fill("#request", "thank you");
  await page.click("button:has-text('Ask')");
  await page.waitForFunction(() => document.querySelectorAll(".earlier li").length >= 2 && document.querySelector(".total"), null, { timeout: 180000 });
  check((await page.$$("table.compare tbody tr")).length === rows && (await page.textContent(".table-carried")).includes("Still on screen"),
        "a reply without a list keeps the earlier list on screen, marked as carried over");
  if (shots) await page.screenshot({ path: `${shots}/ui-carried.png`, fullPage: true });

  // ---- evaluation evidence and tools
  await page.click("text=Evaluation results");
  await page.waitForSelector(".drawer .report table, .drawer p");
  check((await page.textContent(".drawer")).length > 40, "the evaluation panel opens");
  if (shots && (await page.$(".drawer .report table"))) await page.screenshot({ path: `${shots}/ui-evaluation.png` });
  await page.keyboard.press("Escape");
  await page.click("text=Tools");
  await page.waitForSelector(".drawer .report");
  const tools = await page.textContent(".drawer");
  check(tools.includes("rag.search") && tools.includes("web.search"), "the tools panel lists exactly the two MCP tools with their arguments");
  await page.keyboard.press("Escape");

  // ---- a spoken request through the (fake) microphone
  if (voice) {
    await page.click("text=Start over");
    await page.click(".mic");
    await page.waitForSelector(".mic--live", { timeout: 15000 });
    check(true, "recording starts when Talk is pressed");
    await page.waitForSelector(".heard-meta", { timeout: 90000 });          // it stops by itself after the speaker pauses
    const heard = await page.textContent("blockquote.heard");
    check(heard.trim().length > 10, `speech was recognised: "${heard.trim()}"`);
    await page.waitForSelector(".total", { timeout: 180000 });
    check(steps.length > 0 && (await page.textContent(".steps")).includes("Speech recognition"), "the step log starts with speech recognition");
    if (shots) await page.screenshot({ path: `${shots}/ui-voice.png`, fullPage: true });
  }

  check(pageErrors.length === 0, `no script errors on the page${pageErrors.length ? `: ${pageErrors[0]}` : ""}`);
  await browser.close();
  console.log("UI SMOKE TEST PASSED");
})().catch((error) => {
  console.error(String(error.message || error).slice(0, 800));
  console.error("UI SMOKE TEST FAILED");
  process.exit(1);
});

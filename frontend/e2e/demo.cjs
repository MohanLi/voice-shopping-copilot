// Records the short demo at the top of the README: two typed turns through a running server (`voiceshop serve`).
//
//   node frontend/e2e/demo.cjs <frames-folder> [http://127.0.0.1:8000]
//   ffmpeg -f concat -safe 0 -i <frames-folder>/concat.txt -fps_mode vfr -vf \
//     "scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
//     docs/images/demo.gif
//
// It takes a screenshot whenever something may have changed and writes `concat.txt` with how long each one is shown.
// Nothing on the page is staged: the steps, the answer and the highlight that follows the spoken reply are what the
// server sent. Two things are shortened, and the README says so: stretches in which the screen does not change while
// a model or a tool is working are cut to under half a second, and typing is quick.
const { chromium } = require("playwright");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8000";
if (!out) {
  console.error("usage: node frontend/e2e/demo.cjs <frames-folder> [server address]");
  process.exit(2);
}
fs.mkdirSync(out, { recursive: true });

const HOLD = "hold", WAIT = "wait", TYPE = "type", LISTEN = "listen", SCROLL = "scroll";
const SHOWN_MS = { [TYPE]: 50, [WAIT]: 160, [LISTEN]: 185, [SCROLL]: 65 };
const LONGEST_WAIT_MS = 450;       // a stretch in which nothing changes on screen

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const frames = [];               // {file, hash, kind, ms}
  const shot = async (kind, ms) => {
    const file = `f${String(frames.length).padStart(4, "0")}.png`;
    const image = await page.screenshot({ path: path.join(out, file) });
    frames.push({ file, hash: crypto.createHash("md5").update(image).digest("hex"), kind, ms: ms ?? SHOWN_MS[kind] });
  };
  const type = async (text) => {
    await page.click("#request");
    for (const piece of text.match(/.{1,3}/g)) {
      await page.type("#request", piece);
      await shot(TYPE);
    }
    await shot(HOLD, 700);
  };
  const ask = async (earlierTurns, listenFrames) => {
    await page.click("button:has-text('Ask')");
    let done = false;
    const finished = page
      .waitForFunction((n) => document.querySelectorAll(".earlier li").length >= n && document.querySelector(".total"), earlierTurns, { timeout: 180000 })
      .then(() => { done = true; });
    await page.waitForTimeout(150);
    while (!done) {
      await shot(WAIT);
      await page.waitForTimeout(220);
    }
    await finished;
    for (let i = 0; i < listenFrames; i += 1) {          // the reply is being spoken: the highlight follows it
      await page.waitForTimeout(450);
      await shot(LISTEN);
    }
    await shot(HOLD, 1800);
  };
  const scrollTo = async (positions) => {
    for (const y of positions) {
      await page.evaluate((value) => window.scrollTo(0, value), y);
      await shot(SCROLL);
    }
  };

  await page.goto(base);
  await page.waitForSelector(".bar__status span");
  await shot(HOLD, 1400);

  await type("a wooden puzzle for my three year old under twenty dollars");
  await ask(0, 20);
  await scrollTo([90, 180, 270, 360, 450]);
  await shot(HOLD, 3600);
  await scrollTo([360, 270, 180, 90, 0]);

  await type("is the second one cheaper than the first?");
  await ask(1, 14);
  await scrollTo([90, 180, 270, 360]);
  await shot(HOLD, 4200);
  await browser.close();

  // Identical screenshots in a row become one frame; a wait in which nothing changed is cut short.
  const shown = [];
  for (const frame of frames) {
    const last = shown[shown.length - 1];
    if (last && last.hash === frame.hash) {
      last.ms += frame.ms;
      last.waiting = last.waiting && frame.kind === WAIT;
    } else {
      shown.push({ file: frame.file, hash: frame.hash, ms: frame.ms, waiting: frame.kind === WAIT });
    }
  }
  const lines = shown.map((frame) => `file '${frame.file}'\nduration ${((frame.waiting ? Math.min(frame.ms, LONGEST_WAIT_MS) : frame.ms) / 1000).toFixed(3)}`);
  lines.push(`file '${shown[shown.length - 1].file}'`);
  fs.writeFileSync(path.join(out, "concat.txt"), lines.join("\n") + "\n");
  const seconds = shown.reduce((sum, frame) => sum + (frame.waiting ? Math.min(frame.ms, LONGEST_WAIT_MS) : frame.ms), 0) / 1000;
  console.log(`${frames.length} screenshots, ${shown.length} different frames, ${seconds.toFixed(1)} s`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

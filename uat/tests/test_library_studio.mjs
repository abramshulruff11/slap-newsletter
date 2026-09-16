/*
 * Run:  node uat/tests/test_library_studio.mjs
 *
 * library_studio.html edits the two curated libraries directly, with no Python
 * in the loop. That moves two guarantees into the browser, and this checks both
 * against the REAL library files rather than a fixture.
 *
 * 1. FORMATTING. library_json.py exists because the first GIF applier reflowed
 *    the file and turned 19 status changes into a 2,175-line diff. The page
 *    reimplements that formatter in JS; if the port drifts, every save from the
 *    page reflows a library and the diff stops being a record of what the
 *    review decided. So: parse each library, re-serialise with the page's own
 *    function, and demand the bytes back unchanged.
 *
 * 2. VALIDATION. The page refuses to save a library that would fail
 *    uat/tests/test_meme_library.py. That promise is only worth something if
 *    the JS checks actually fire, so each one is given a deliberately broken
 *    library and must catch it. A validator that passes everything passes a
 *    broken library too.
 *
 * The functions are extracted from the HTML rather than duplicated here — a
 * copy would drift the same way the thing it is testing would.
 *
 * No network, no API calls.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = p => fs.readFileSync(path.join(REPO, p), "utf8");

const html = read("library_studio.html");
const src = html.slice(html.indexOf('<script>\n"use strict"') + 8, html.lastIndexOf("</script>"));

// The pure, DOM-free half of the page: the formatter constants, then everything
// from expandBoxes down to the end of validateGifs.
const consts = src.slice(src.indexOf("const STRING_ARRAY"),
                         src.indexOf("/* ====", src.indexOf("const STRING_ARRAY")));
const a = src.indexOf("function expandBoxes");
const b = src.indexOf("const validate =", a);
if (a < 0 || b < 0) throw new Error("could not locate the pure helpers in library_studio.html");

const F = new Function(consts + "\n" + src.slice(a, b) +
  "\nreturn {dumpsMatchingStyle, usesCompactArrays, validateMemes, validateGifs};")();

let failures = 0;
function check(label, cond, extra = "") {
  console.log(`  [${cond ? "ok " : "FAIL"}] ${label}${!cond && extra ? " — " + extra : ""}`);
  if (!cond) failures++;
}

console.log("=".repeat(70));
console.log("LIBRARY STUDIO — formatter round-trip + validator parity");
console.log("=".repeat(70));

// --- 1. byte-for-byte round-trip -------------------------------------------
for (const [name, file, compactExpected] of [
  ["meme", "prompts/meme_library.DRAFT.json", false],
  ["gif", "prompts/gif_library.DRAFT.json", true],
]) {
  const raw = read(file);
  // No trailing newline is appended: library_json.py does not add one either,
  // and both files on disk end at the closing brace.
  const out = F.dumpsMatchingStyle(JSON.parse(raw), raw);
  check(`${name}: page's writer reproduces the file byte-for-byte`, out === raw,
        `${out.length} vs ${raw.length} bytes`);
  check(`${name}: compact-array convention detected correctly`,
        F.usesCompactArrays(raw) === compactExpected);
  if (out !== raw) {
    const x = out.split("\n"), y = raw.split("\n");
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if (x[i] !== y[i]) {
        console.log(`        first difference, line ${i + 1}:`);
        console.log(`          page: ${JSON.stringify(x[i])}`);
        console.log(`          disk: ${JSON.stringify(y[i])}`);
        break;
      }
    }
  }
}

// --- 2. the validators pass what is actually shipping -----------------------
const meme = JSON.parse(read("prompts/meme_library.DRAFT.json"));
const gif = JSON.parse(read("prompts/gif_library.DRAFT.json"));
const me = F.validateMemes(meme), ge = F.validateGifs(gif);
check(`meme validator passes the live ${meme.templates.length}-template library`,
      me.length === 0, me.slice(0, 4).join(" | "));
check("gif validator passes the live GIF library", ge.length === 0, ge.slice(0, 4).join(" | "));

// --- 3. …and actually catch the failures they claim to ----------------------
// Each mutation reproduces a bug this repo has really shipped.
const broken = (mutate, lib = meme) => { const c = structuredClone(lib); mutate(c); return c; };

check("catches box_count disagreeing with boxes[]",
  F.validateMemes(broken(l => { l.templates[0].box_count = 9; }))
   .some(e => e.includes("box_count=9")));

check("catches placement 'copy' while a box still claims the SUBJECT slot",
  F.validateMemes(broken(l => {
    l.templates.find(t => (t.subject.placement || "").startsWith("box:")).subject.placement = "copy";
  })).some(e => e.includes("claims the SUBJECT slot")));

check("catches valence and placement disagreeing about the subject box",
  F.validateMemes(broken(l => {
    const t = l.templates.find(t => (t.subject.placement || "").startsWith("box:") && t.box_count > 1);
    t.subject.placement = "box:" + ((+t.subject.placement.slice(4) + 1) % t.box_count);
  })).some(e => e.includes("placement says") || e.includes("valence puts the SUBJECT")));

check("catches a short worked example (the meme_box_check drop)",
  F.validateMemes(broken(l => { l.templates[0].worked_example.boxes = ["only one"]; }))
   .some(e => e.includes("worked example")));

check("catches a valence rule citing a box the template does not have",
  F.validateMemes(broken(l => { l.templates[0].valence = "Box 7 MUST be funny."; }))
   .some(e => e.includes("valence cites box 7")));

check("catches an engine missing from _meta.engines",
  F.validateMemes(broken(l => { l.templates[0].engine = "no_such_engine"; }))
   .some(e => e.includes("not defined in _meta.engines")));

check("catches a duplicate slug",
  F.validateMemes(broken(l => { l.templates.push(structuredClone(l.templates[0])); }))
   .some(e => e.includes("duplicate slug")));

check("catches a non-numeric template_id",
  F.validateMemes(broken(l => { l.templates[0].template_id = "drake"; }))
   .some(e => e.includes("numeric Imgflip id")));

// The most consequential meme bug this repo has: meme_box_check.py scores the
// writer's captions against IMGFLIP's box_count, not the library's, and drops a
// short meme rather than shipping a blank panel. So a library that disagrees
// with Imgflip loses every meme of that template, silently. 16 templates were
// corrected for this in Aug/Sep 2026.
check("catches the library disagreeing with Imgflip's box count",
  F.validateMemes(meme, {[String(meme.templates[0].template_id)]: meme.templates[0].box_count + 1})
   .some(e => e.includes("Imgflip says")));

check("says nothing when the library and Imgflip agree",
  F.validateMemes(meme, Object.fromEntries(
    meme.templates.map(t => [String(t.template_id), t.box_count]))).length === 0);

check("skips the Imgflip check for templates it has no live count for",
  F.validateMemes(meme, {}).length === 0);

check("catches an invalid GIF status",
  F.validateGifs(broken(l => {
    l.categories[Object.keys(l.categories)[0]].gifs[0].status = "maybe";
  }, gif)).some(e => e.includes("status must be")));

check("catches the same Giphy id in two categories",
  F.validateGifs(broken(l => {
    const [c1, c2] = Object.keys(l.categories);
    l.categories[c2].gifs.push(structuredClone(l.categories[c1].gifs[0]));
  }, gif)).some(e => e.includes("appears in both")));

console.log();
if (failures) {
  console.log("=".repeat(70));
  console.log(`${failures} FAILURE(S)`);
  console.log("=".repeat(70));
  process.exit(1);
}
console.log("=".repeat(70));
console.log("ALL CHECKS PASSED — 0 API calls");
console.log("=".repeat(70));

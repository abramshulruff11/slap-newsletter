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
  "\nreturn {dumpsMatchingStyle, usesCompactArrays, validateMemes, validateGifs," +
  " gifTagErrors, applyGifTagEdit, setGifStatus, blankGifTags, GIF_TAG_FIELDS};")();

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

// --- 4. GIF tags (SLA-137) --------------------------------------------------
// The tagger's fields are merged into the library; a page save must neither
// drop them nor let a hand edit write something SLA-138's selection cannot read.
const allGifs = Object.values(gif.categories).flatMap(c => c.gifs);
const tagged = allGifs.filter(g => "moods" in g);
const vocab = gif._meta.tag_vocabulary;
check("the live library carries tags and their vocabulary",
      tagged.length > 0 && vocab && vocab.subcategories.length > 0, `${tagged.length} tagged`);
check("every rejected GIF has a reject_reason, and only rejected ones do",
      allGifs.every(g => (g.status === "rejected") === ("reject_reason" in g)));

// A save is serialise(parse(file)): the round-trip above already proves no tag
// is lost, but say it directly for the fields SLA-137 added.
{
  const raw = read("prompts/gif_library.DRAFT.json");
  const again = JSON.parse(F.dumpsMatchingStyle(JSON.parse(raw), raw));
  const fields = l => Object.values(l.categories).flatMap(c => c.gifs)
    .map(g => JSON.stringify(F.GIF_TAG_FIELDS.map(k => g[k]))).join("\n");
  check("a save keeps every tag field of every GIF", fields(again) === fields(gif));
}

const firstTagged = l => Object.values(l.categories).flatMap(c => c.gifs)
  .find(g => "moods" in g && g.status === "verified");
const gifErr = (mutate, needle) => F.validateGifs(broken(l => mutate(firstTagged(l), l), gif))
  .some(e => e.includes(needle));

check("catches an unknown mood", gifErr(g => g.moods.push("vibes"), 'unknown mood "vibes"'));
check("catches an unknown situation",
      gifErr(g => g.subcategories.push({id: "no_such_situation", fit: "strong"}), "unknown situation"));
check("catches a situation fit that is not strong/partial",
      gifErr(g => { g.subcategories = [{id: vocab.subcategories[0], fit: "great"}]; }, "fit must be"));
check("catches the same situation twice",
      gifErr(g => { g.subcategories = [{id: vocab.subcategories[0], fit: "strong"},
                                       {id: vocab.subcategories[0], fit: "partial"}]; }, "listed twice"));
check("catches more than three situations",
      gifErr(g => { g.subcategories = vocab.subcategories.slice(0, 4).map(id => ({id, fit: "partial"})); },
             "at most 3"));
check("catches a bad intensity", gifErr(g => { g.intensity = "extreme"; }, "intensity must be"));
check("catches a bad reaction_readiness",
      gifErr(g => { g.reaction_readiness = "sure"; }, "reaction_readiness must be"));
check("catches a bad confidence", gifErr(g => { g.confidence = "certain"; }, "confidence must be"));
check("catches entities.players that is not a list",
      gifErr(g => { g.entities.players = "LeBron"; }, "entities.players must be a list"));
check("catches a partly tagged GIF (the half-written entry SLA-138 would trip on)",
      gifErr(g => { delete g.intensity; }, "partly tagged, missing intensity"));
check("catches tags with no vocabulary to check them against",
      gifErr((g, l) => { delete l._meta.tag_vocabulary; }, "no _meta.tag_vocabulary"));
check("catches a rejected GIF with no reason",
      gifErr(g => { g.status = "rejected"; delete g.reject_reason; }, "needs a reject_reason"));
check("catches a reject_reason left on a GIF that is back in rotation",
      gifErr(g => { g.reject_reason = "off_topic"; }, "only for a rejected GIF"));
check("accepts 'rejected' as a status", !F.validateGifs(gif).some(e => e.includes("status must be")));

// The edit helpers, as the page calls them.
{
  const g = structuredClone(firstTagged(gif));
  const day = "2026-10-17";
  const before = JSON.stringify(g.moods);
  const m = vocab.moods.find(x => !g.moods.includes(x));
  F.applyGifTagEdit(g, "moods.toggle", m, day, vocab);
  check("ticking a mood adds it", g.moods.includes(m));
  F.applyGifTagEdit(g, "moods.toggle", m, day, vocab);
  check("ticking it again restores the original list exactly", JSON.stringify(g.moods) === before);
  check("a hand edit is recorded in tagged_by.edited", g.tagged_by.edited === day);
  F.applyGifTagEdit(g, "entities.teams", "Lakers, , Celtics ", day, vocab);
  check("entity lists split on commas and drop blanks",
        JSON.stringify(g.entities.teams) === '["Lakers","Celtics"]');
  g.subcategories = [];
  F.applyGifTagEdit(g, "subcategories.add", vocab.subcategories[5], day, vocab);
  F.applyGifTagEdit(g, "subcategories.0.fit", "strong", day, vocab);
  check("a situation can be added and its fit set",
        g.subcategories[0].id === vocab.subcategories[5] && g.subcategories[0].fit === "strong");
  F.applyGifTagEdit(g, "subcategories.del", "0", day, vocab);
  check("…and removed", g.subcategories.length === 0);
  F.setGifStatus(g, "rejected");
  check("rejecting by hand fails until a reason is picked", F.gifTagErrors(g, vocab).length > 0);
  F.applyGifTagEdit(g, "reject_reason", "off_topic", day, vocab);
  check("…and passes once one is", F.gifTagErrors(g, vocab).length === 0,
        F.gifTagErrors(g, vocab).join(" | "));
  F.setGifStatus(g, "verified");
  check("putting it back in rotation drops the reject_reason", !("reject_reason" in g));
  const fresh = {id: "abc123", label: "x", status: "candidate", ...F.blankGifTags(day)};
  check("a GIF tagged by hand from blank validates", F.gifTagErrors(fresh, vocab).length === 0,
        F.gifTagErrors(fresh, vocab).join(" | "));
  let threw = false;
  try { F.applyGifTagEdit(g, "label", "x", day, vocab); } catch { threw = true; }
  check("the tag editor refuses a non-tag field", threw);
}

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

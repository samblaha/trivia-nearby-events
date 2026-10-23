import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { execFileSync } from "node:child_process";

const html = execFileSync("python3", ["-c", "from invite_landing import INVITE_HTML; print(INVITE_HTML)"], {
  cwd: new URL(".", import.meta.url), encoding: "utf8",
});
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const token = "12345678-1234-4123-8123-123456789abc";

async function page(path, search, response = { name: "Quiz Crew", member_count: 2 }, status = 200) {
  const elements = new Map();
  const requests = [];
  const element = id => {
    if (!elements.has(id)) {
      const classes = new Set(["open", "copy"].includes(id) ? ["hidden"] : []);
      elements.set(id, { textContent: "", href: "#", classList: {
        add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      } });
    }
    return elements.get(id);
  };
  vm.runInNewContext(script, {
    document: { getElementById: element },
    location: { pathname: path, search, href: "https://example.test" + path + search },
    URLSearchParams, TextDecoder, Uint8Array, atob, Intl, Date, Number, AbortSignal,
    navigator: { clipboard: { writeText: async () => {} } },
    fetch: async (url, options) => {
      requests.push(JSON.parse(options.body));
      return { ok: status === 200, status, json: async () => response };
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  return { element, requests };
}

test("team invitation preserves event and only fetches a public preview", async () => {
  const p = await page("/invite/team", "?token=" + token + "&event=" + token);
  assert.equal(p.element("open").href, "trivianearby://team?token=" + token + "&event=" + token);
  assert.equal(p.element("title").textContent, "Quiz Crew");
  assert.equal(p.element("open").classList.contains("hidden"), false);
  assert.deepEqual(p.requests.map(x => x.action), ["preview"]);
});
test("revoked invitation disables app handoff", async () => {
  const p = await page("/invite/team", "?token=" + token, {}, 403);
  assert.equal(p.element("open").classList.contains("hidden"), true);
  assert.match(p.element("title").textContent, /isn’t available/);
});
test("malformed and repeated tokens do not fetch or open", async () => {
  for (const query of ["?token=javascript:alert(1)", "?token=" + token + "&token=" + token]) {
    const p = await page("/invite/team", query);
    assert.equal(p.requests.length, 0);
    assert.equal(p.element("open").classList.contains("hidden"), true);
  }
});
test("casual event details decode Unicode and do not submit suggested RSVPs", async () => {
  const payload = { id: "event1", t: "Café trivia", v: "The Pub", a: "1 Main St", s: "2026-10-29T23:00:00Z", tz: "America/New_York" };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const p = await page("/invite", "?p=" + encoded + "&r=going");
  assert.equal(p.element("title").textContent, "Café trivia");
  assert.equal(p.element("open").href, "trivianearby://invite?p=" + encoded);
  assert.equal(p.requests.length, 0);
  assert.match(p.element("detail").textContent, /The Pub/);
});
test("untrusted team names are treated as text", async () => {
  const name = '<img src=x onerror="alert(1)">';
  const p = await page("/invite/team", "?token=" + token, { name, member_count: 2 });
  assert.equal(p.element("title").textContent, name);
  assert.equal(p.element("title").innerHTML, undefined);
});

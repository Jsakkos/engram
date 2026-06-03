// Run with: node --test .github/scripts/contributor-greeting.test.cjs
// (the directory-glob form is unreliable on Windows/Node 24, so target the
// file directly). Uses Node's built-in test runner — no npm install required.
const test = require("node:test");
const assert = require("node:assert");
const { decide } = require("./contributor-greeting.cjs");

test("owner / member / collaborator are skipped", () => {
  assert.equal(decide("OWNER", "Jsakkos"), "skip");
  assert.equal(decide("MEMBER", "someone"), "skip");
  assert.equal(decide("COLLABORATOR", "someone"), "skip");
});

test("bots are skipped regardless of association", () => {
  assert.equal(decide("CONTRIBUTOR", "dependabot[bot]"), "skip");
  assert.equal(decide("CONTRIBUTOR", "renovate"), "skip");
  assert.equal(decide("FIRST_TIME_CONTRIBUTOR", "github-actions"), "skip");
});

test("first-time external contributor gets the first-timer greeting", () => {
  assert.equal(decide("FIRST_TIME_CONTRIBUTOR", "katelovescode"), "first");
});

test("returning external contributor gets repeat thanks", () => {
  assert.equal(decide("CONTRIBUTOR", "katelovescode"), "repeat");
});

const { renderComment } = require("./contributor-greeting.cjs");

test("first-timer comment mentions the login, CONTRIBUTORS.md and CONTRIBUTING.md", () => {
  const msg = renderComment("first", "katelovescode");
  assert.ok(msg.includes("@katelovescode"));
  assert.ok(msg.includes("CONTRIBUTORS.md"));
  assert.ok(msg.includes("CONTRIBUTING.md"));
});

test("repeat comment mentions the login and release notes", () => {
  const msg = renderComment("repeat", "katelovescode");
  assert.ok(msg.includes("@katelovescode"));
  assert.ok(msg.includes("release"));
});

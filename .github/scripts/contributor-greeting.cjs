// Pure decision + message templates for the contributor-welcome workflow.
// Dependency-free and standalone so it can be unit-tested with
// `node --test .github/scripts/` (Node's built-in runner, no npm install).

const INTERNAL = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
const BOT_LOGINS = new Set(["dependabot", "renovate", "github-actions"]);

function isBot(login) {
  const lowered = String(login).toLowerCase();
  return lowered.endsWith("[bot]") || BOT_LOGINS.has(lowered);
}

/** Returns "first" | "repeat" | "skip". */
function decide(association, login) {
  if (isBot(login) || INTERNAL.has(association)) return "skip";
  if (association === "FIRST_TIME_CONTRIBUTOR") return "first";
  return "repeat";
}

function renderComment(action, login) {
  if (action === "first") {
    return [
      `🎉 Thank you for your first contribution to Engram, @${login}!`,
      ``,
      `Your work will be credited in the next release's notes and added to ` +
        `[CONTRIBUTORS.md](../blob/main/CONTRIBUTORS.md). If you'd like to keep ` +
        `contributing, the [contributing guide](../blob/main/CONTRIBUTING.md) ` +
        `has everything you need to get a dev environment running.`,
      ``,
      `Welcome aboard! 🚀`,
    ].join("\n");
  }
  return (
    `Thanks again for another contribution, @${login}! 🙌 ` +
    `It'll be credited in the next release's notes.`
  );
}

module.exports = { decide, renderComment, isBot };

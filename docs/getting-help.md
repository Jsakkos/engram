# Getting Help & Reporting Issues

The quickest way to get help, report a bug or ask for a feature is **Trace**, the support bot
in the [Engram Discord](https://discord.gg/G8fjjGswdc). Trace answers from this
documentation, helps you troubleshoot, and turns bug reports and feature requests into
GitHub issues for you. You can still [open an issue on GitHub](#reporting-on-github-directly)
yourself if you prefer.

Trace is an AI assistant (Claude). It knows the user-facing docs and the latest release
notes, but it can be wrong. If an answer doesn't fit what you see, say so in the thread or
react 👎; maintainers review answers marked unhelpful.

## Asking a question

| Where | What happens |
|---|---|
| Post in the **`#ask-engram`** forum | Trace answers every new post, then keeps replying to you in that post. Anyone else who joins in needs to @mention it. |
| `@Trace` in any text channel | Trace opens a thread from your message and answers there. |
| `/ask` | A private answer only you can see. Good for a quick question; it can't file issues. |

When a maintainer replies in a thread, Trace steps back until someone mentions it again. To
turn Trace off in your own thread (or back on), use `/quiet`.

## Reporting a bug

1. Run `/bug` in the Engram server. A short form asks for a one-line summary and the
   details: what happened, what you expected, and your Engram version and OS if you know them.
2. Trace opens a post in `#ask-engram` with your report (tagged **Bug** or **Feature**) and asks for anything it still needs.
3. **Attach a diagnostics bundle** if the problem involves a disc or job (see
   [below](#attaching-a-diagnostics-bundle)). It answers most of Trace's questions up front.
4. When it has enough, Trace searches existing issues and shows you a **draft** with three
   buttons:
    - **File issue** creates the GitHub issue. If Trace found the same problem already
      reported, the button reads **Add to issue** and adds your report as a comment there.
    - **Edit** lets you correct the draft first.
    - **Cancel** throws it away.

Nothing is filed until you (or a maintainer) press the button. You can also start in
`#ask-engram` or with `@Trace` and ask it to file the problem as a bug; the same draft and
buttons appear.

## Requesting a feature

Run `/feature`. The form asks what you're trying to do and what would help. From there it
works like a bug report: Trace talks it through with you, checks for an existing request,
and shows a draft for you to file.

Describing the *problem* ("I have to rename every bonus feature by hand") is more useful
than describing a solution, because it leaves room for a better fix.

## Attaching a diagnostics bundle

From the dashboard, open the job's detail view and download its diagnostic `.zip` (see
[Diagnostics bundle](troubleshooting.md#diagnostics-bundle)), then drop the file into your
Discord thread.

Trace reads the versions, the job's state, errors and warnings from its log, and problems in
the MakeMKV logs, so it can troubleshoot without asking for things the bundle already says.
When your report is filed, the bundle's summary and a log excerpt are attached to the issue
in a collapsed section, so maintainers see the evidence word for word.

Secrets (API keys, tokens, webhook URLs) and your home folder path are redacted, both by
Engram when it builds the bundle and again by Trace. If you'd rather the bundle stay out of
the issue, tell Trace before you file.

## After your issue is filed

Stay in the thread. Trace checks GitHub every few minutes and posts there when:

- a maintainer comments on your issue. The comment is quoted with a **Reply on GitHub**
  button, so you can answer without a GitHub account;
- the issue is closed (fixed, not planned, or a duplicate) or reopened;
- a release that includes the fix comes out.

To check on any issue yourself, run `/issue` with its number.

## Good to know

- Filing is limited to a few issues per person per day, and Discord accounts younger than a
  week can chat with Trace but not file. Maintainers can always file for you.
- Trace only works in the Engram server, not in DMs or other servers.
- If Trace has hit its daily budget it says so and is back the next day. A maintainer can
  still help in the meantime.

## Reporting on GitHub directly

If you'd rather not use Discord, [open an issue on GitHub](https://github.com/Jsakkos/engram/issues/new).
Include:

- your Engram version and OS;
- what you did, what you expected, and what happened instead;
- the [diagnostics bundle](troubleshooting.md#diagnostics-bundle) for the job, if a disc was
  involved.

Check [Troubleshooting](troubleshooting.md) first; many common setup problems are covered
there.

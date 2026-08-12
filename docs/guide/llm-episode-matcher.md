# LLM Episode Matcher

An opt-in fallback for TV episode identification. When Engram's primary audio-fingerprint matcher can't confidently identify which episode a ripped disc title is, the LLM matcher sends the cleaned transcript plus the candidate season's TMDB synopses to your configured AI provider, and surfaces a suggested episode through the review queue. **The LLM never auto-organizes** — every suggestion requires your confirmation.

## When it runs

**Automatically**, when:

- TV-content matching is needed,
- The primary audio-fingerprint matcher returns confidence < 0.7 (or no match),
- `ai_episode_matching_enabled` is on and you've configured an API key,
- The season is known from the disc volume label.

**On demand**, via the **Try AI match** button on any title in the review queue.

When the season can't be determined from the disc, the LLM matcher is skipped — accuracy collapses without season narrowing.

## Enabling it

1. Open **Settings** → **Preferences**.
2. Pick an **AI Provider** and paste your **API key**. The same provider/key is shared with AI-Powered Title Resolution (if you've enabled that).
3. Check **AI-Powered Episode Matching (TV)**.
4. Optionally set **Model**. Leave it blank to use Engram's default for the provider.

Use **Test Connection** after step 2 — it runs a real completion against the
provider, so it catches a key that cannot reach the model before a disc does.

See [Configuration](../getting-started/configuration.md) for the full settings reference.

### If your key can't reach the default model

Not every key has access to every model. A Gemini key whose Google Cloud project
does not carry `gemini-2.5-flash-lite`, for example, gets an HTTP 404 rather than
an auth error, because Google resolves the credential *before* the model — so
reaching that error means the key itself is fine.

Engram reports this as "This key does not have access to the model Engram uses for
Gemini", then lists the models your key *can* use. Put one of those in the
**Model** field.

### Running behind a reverse proxy

**Test Connection** is restricted to the machine running Engram, because each
press spends money on your key. In Docker or any reverse-proxied deployment the
request arrives from the proxy rather than from loopback, so the button reports
that LAN access is required. Enable **Allow LAN access** in Settings → Network to
use it from another device.

## Provider recommendation

**Gemini Flash-Lite** has the best accuracy/$ on this task in our internal evals (66-73% top-1 vs ~49% for Anthropic Haiku 4.5 on a comparable dataset). Get a free-tier key at <https://aistudio.google.com/apikey>.

Anthropic, OpenAI, and OpenRouter also work and remain useful for [AI-powered title resolution](../getting-started/configuration.md), so you don't need to switch providers if you've already configured one of those.

## Accuracy expectations

The LLM matcher relies on the distinctiveness of TMDB synopses, so accuracy varies sharply by show type.

| Show category | Typical accuracy | Examples |
|---|---|---|
| Episodic / procedural with distinct plots | 90-100% | Star Trek: TNG, Arrow, Breaking Bad, Adventure Time, 9-1-1 |
| Mixed serialized/episodic | 60-80% | The Expanse, Anne with an E, AHS |
| Framing-device serialization (synopses overlap) | 25-35% | 13 Reasons Why, All of Us Are Dead, Arcane |

In all cases the suggestion lands in the review queue — accuracy mainly affects how many one-click confirmations you do vs. how many manual selections.

## Privacy

The cleaned dialogue transcript for the episode is sent over the network to your configured AI provider. If that matters to you, leave this feature off — it's disabled by default.

## Cost

Sub-cent per episode at Gemini Flash-Lite pricing. The free tier covers typical home use comfortably.

## Confirmation requirement

Suggestions always appear in the review queue with an **Accept AI suggestion** button. The LLM never writes to your library directly; you stay in control of what gets organized.

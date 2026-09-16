# wisp-mail

A focused, local AI email triage assistant for macOS Mail.app — the mail-only
sibling of [wisp-agent](https://github.com/andrewlj/wisp-agent), a general
personal assistant. wisp-mail does one thing: understand your email and act
on it according to preferences you've actually approved.

Not a chat bot. Not resident. Triggered on a schedule (`launchd`), runs once,
exits. No cloud dependencies — talks to the same local OpenAI-compatible LLM
server wisp-agent uses.

## Why a separate project, not another wisp-agent tool

wisp-agent already has mail tools, general-purpose and reactive. wisp-mail
exists because "understand every email individually before deciding" doesn't
fit an interactive chat turn budget, and a real incident showed what happens
when a broad tool gets reached for instead: a request to clean up inbox spam
once wiped an entire 2158-message inbox. wisp-mail's answer is structural,
not a bigger prompt warning:

- **Every email gets its own isolated LLM classification call** — no
  sender/keyword shortcut decides "junk or not" on your behalf. Same sender,
  different content, can land in different categories (a bank's routine
  statement vs. its fraud alert aren't treated the same way).
- **Each classification call is stateless** — a fresh system + user message
  per email, never a growing conversation. One email's judgment can't leak
  into the next.
- **Understanding is cached permanently; deciding isn't.** A message's
  content doesn't change, so it's classified once, ever. What ACTION a
  category maps to is a separate, revisable decision, re-evaluated against
  your current preferences every run.
- **Nothing acts on your mailbox without your approval.** Approval lives in
  the data flow (a reviewed rule table), not in a prompt instruction a model
  could talk itself past under pressure.

Full design rationale, the four-stage pipeline, and the eventual
wisp-agent integration plan are written up in the project's design doc
(ask in the wisp-agent repo if you don't have the link).

## Status

**Phase 1 — understand only, zero side effects.** Fetches unread mail,
classifies each new message, caches the result, prints it. Does not move,
delete, or modify anything. This is deliberately the first thing built and
proven before any action layer exists.

Not yet built: the preference/rule table, the candidate-review queue, actual
execution (move-to-Junk etc.), scheduling, and the Telegram report push. See
the design doc's phased roadmap.

## Setup

**1. Install dependencies**

```bash
git clone git@github.com:andrewlj/wisp-mail.git
cd wisp-mail
pip install -r requirements.txt
```

**2. Configure**

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` — point `server`/`model` at the same local LLM server
wisp-agent uses (they can share one omlx/mlx_lm instance):

```yaml
server:
  base_url: "http://127.0.0.1:8000/v1/chat/completions"
  api_key: "your-api-key"

model:
  name: "Qwen3.5-9B-MLX-8bit"

mail:
  per_run_limit: 50   # new (uncached) messages classified per run — a large
                      # backlog is worked through gradually, not in one shot
```

**3. Run**

```bash
python3.11 run.py                    # classify up to per_run_limit new unread
python3.11 run.py --limit 10         # override the cap for this run
python3.11 run.py --account you@example.com
```

Each message prints its category, confidence, and one-line reasoning. Run it
again and already-classified messages are skipped — no repeat LLM calls.

## Project structure

```
wisp-mail/
├── agent_core.py      # LLM HTTP layer (retry) + one-way Telegram push —
│                       # trimmed from wisp-agent's agent.py: no streaming/
│                       # tool-calling machinery, classification is a single
│                       # non-streaming structured call
├── mail_tools.py       # macOS Mail.app access over AppleScript — locale-aware
│                       # mailbox resolution, ported from wisp-agent's tools.py.
│                       # Phase 1: list + read only
├── classify.py         # single-email, stateless LLM classification
├── preferences.py      # classification cache (Phase 1); rule table +
│                       # candidate-review queue land in Phase 2
├── run.py              # entry point — what launchd will eventually call
├── config.yaml          # server/model/telegram config (git-ignored)
├── config.example.yaml  # config template
├── requirements.txt
└── LICENSE
```

Data lives under `~/wisp-mail/` (configurable via `workspace` in
`config.yaml`) — currently just `cache/`, one JSON file per classified
message, keyed by a hash of its message-id.

## License

MIT

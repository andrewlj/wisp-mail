"""
wisp-mail agent core — LLM HTTP layer + one-way Telegram push.

Extracted from wisp-agent's agent.py and deliberately simplified: wisp-mail
has no ReAct tool-calling loop (understanding is a single structured
classification call, execution is deterministic Python code calling
mail_tools directly — see classify.py / run.py), so this drops the
tool-schema plumbing and the streaming watchdog machinery that agent.py
needs for a live, multi-turn chat.

Kept from agent.py, unchanged in spirit:
  - _llm_post()'s retry-on-transient-failure behaviour (connection errors,
    read timeouts, 5xx — never on 4xx).
  - _telegram_send()'s one-way push with 4096-char chunking.

NOT ported (streaming, STREAM_TIMEOUT watchdog, server-abort-as-content
detection): all of that exists specifically to bound a STREAMING response
whose read_timeout only bounds the gap *between* chunks, not the total wait
when zero chunks ever arrive. wisp-mail's classification calls are plain
non-streaming completions (stream=False) — a single request whose whole
duration is already bounded synchronously by _llm_post's own connect/read
timeout, so that failure mode doesn't apply here.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests
import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

BASE_URL = _cfg["server"]["base_url"]
API_KEY  = _cfg["server"]["api_key"]
MODEL    = _cfg["model"]["name"]

TELEGRAM_BOT_TOKEN = str(_cfg.get("telegram", {}).get("bot_token") or "")
TELEGRAM_USER_ID   = str(_cfg.get("telegram", {}).get("user_id") or "").strip()

_MAX_RETRIES  = 3
_RETRY_DELAYS = (1, 2, 4)   # seconds between attempts


def _llm_post(payload: dict) -> requests.Response:
    """POST a non-streaming request to the LLM endpoint with exponential-
    backoff retry on transient errors.

    Retries on:  connection error, read timeout, HTTP 5xx
    No retry on: HTTP 4xx (bad request / auth failure — retrying won't help)

    Timeout: (connect=10s, read=120s) — a single non-streaming call's whole
    duration is bounded by this; unlike a streaming response, there's no
    "gap between chunks" loophole to close with a separate watchdog.
    """
    last_err: Exception = RuntimeError("LLM request failed")

    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            delay = _RETRY_DELAYS[attempt - 1]
            print(f"  ⟳ LLM unreachable — retry {attempt}/{_MAX_RETRIES} in {delay}s…",
                  flush=True)
            time.sleep(delay)
        try:
            resp = requests.post(
                BASE_URL,
                headers={"Authorization": f"Bearer {API_KEY}"},
                json=payload,
                stream=False,
                timeout=(10, 120),
            )
            if resp.status_code >= 500:
                last_err = requests.exceptions.HTTPError(
                    f"server error {resp.status_code}", response=resp)
                continue          # retry on 5xx
            resp.raise_for_status()   # raises immediately on 4xx
            return resp
        except requests.exceptions.HTTPError:
            raise                     # 4xx → propagate, no retry
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last_err = e
            continue

    raise last_err   # exhausted all retries


def call_llm(messages: list, max_tokens: int = 512, json_mode: bool = False) -> str:
    """Single non-streaming LLM call; returns the content string."""
    payload = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
               "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = _llm_post(payload)
    return resp.json()["choices"][0]["message"]["content"]


def telegram_send(text: str, chat_id: str | None = None) -> bool:
    """One-way push to Telegram (defaults to the configured user). Splits
    long text to fit the 4096-char limit. Returns True on success. No
    polling/gateway — wisp-mail never reads replies."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    chat = str(chat_id or TELEGRAM_USER_ID).strip()
    if not chat:
        return False
    text = text or "(empty)"
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 3900:
            if buf:
                chunks.append(buf)
            buf = line[:3900]
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    ok = True
    for chunk in chunks or [text[:3900]]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": chat, "text": chunk}, timeout=20)
            ok = ok and r.status_code == 200
        except Exception:
            ok = False
    return ok

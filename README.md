# LUFY + self-disclosure: user study system

Private. Unpublished research — do not push to a public remote.

A website where each participant chats, at home, with two chatbots — "LUFY A"
and "LUFY B" — one per condition, both running the original LUFY
(Sumida, Inoue & Kawahara, D&D 2025):

| Bot | Condition | Forgetting |
|-----|-----------|------------|
| A   | `lufy`    | original `forget_mechanism_wisely` |
| B   | `lufy_sd` | `forget_mechanism_sd_first`: self-disclosure memories are kept first; how many memories are kept and every survival probability are unchanged |

The bot-to-condition mapping lives only in each engine's `.env`; participants
see two bots, never a condition name. Swap the two `.env` files to change it.

```
phone / laptop ──https──▶ portal :8000 (public via Tailscale Funnel)
                             ├──▶ engine A :8001 (127.0.0.1 only, runs/A)
                             └──▶ engine B :8002 (127.0.0.1 only, runs/B)
```

## What changed vs. LUFY

`lufy/` is LUFY-EVO `Code/` (= upstream LUFY plus a `perplexity.py`
compatibility fix) at commit `86af756`, unmodified. Everything else is new:

- `study/engine.py` — `chat.py`'s loop driven by one message at a time. Same
  constants, cutoffs, prompts and order of side effects; see its docstring for
  the three differences (state persisted, time-mention coin redrawn per session,
  SD tagging before forgetting). All three are identical across conditions.
- `study/forget_sd_first.py` — the manipulation. `tests/test_forget_equivalence.py`
  checks that with no SD labels it keeps exactly what the original keeps.
- `study/sd_tagging.py`, `study/sd_detector.py` — SD labels per memory, stored as
  `sd_labels.json` keyed by sha256 of the memory text (as in LUFY-EVO E035).
  Runs after each session in **both** conditions (control: logged only), so reply
  latency is identical and SD retention can be compared.
  The detector is E033 v2, ported verbatim (prompt checked character for
  character): `gpt-4o-2024-08-06`, user words only, 20 per call, Dou et al.'s 19
  categories, `self_disclosure` scope. Frozen 2026-11-21; blind test set
  precision 0.55, recall 0.75, kappa 0.59. Not re-validated in this study.
- `study/service.py`, `study/engine_server.py` — one condition as a local JSON
  service: per-participant lock, end-of-session processing in one background
  worker, idle sessions closed after 20 min, recovery after a restart, turn log.
- `study/portal.py`, `study/schedule.py`, `study/templates/` — the website.
- `study/line_bot.py` — the earlier LINE front end (reply API only). Kept, not used.

## Participant flow

Each participant gets a personal link (`python -m study.admin links`). The page
always shows the one next step (rules in `study/schedule.py`):

1. Chat with the participant's `first` bot for about 30 minutes. A timer shows
   the time; at 30 minutes a banner says they can end, nothing is cut off.
2. "End chat" → 3 question-answer pairs about what they told that bot (LUFY's
   protocol). Required before anything else.
3. Same day: the other bot, then its 3 pairs. Bots alternate, first bot first.
4. At most one session per bot per day (Tokyo time), so 4 sessions per bot take
   at least 4 days. Next day: same link.
5. After 4 sessions with each bot: the questionnaire in `runs/questionnaire.json`
   (if it has items), then done.

A session idle for 20 minutes is closed by the engine; the portal notices on
the next page load and asks for that session's pairs. A session ended with no
messages does not count. While a bot is still processing the last session,
starting the same bot again is refused with a "try again in a few minutes".

## Setup

```
runs/
  participants.json      # copy participants.example.json; names letters only, unique
  questionnaire.json     # copy questionnaire.example.json; add items once decided
  logs/                  # bot_A.jsonl, bot_B.jsonl (every turn, with latency)
  A/.env  B/.env         # copy .env.example; OPENAI_API_KEY, CONDITION
  portal/.env            # copy .env.example
  portal/data/           # one JSON per participant: sessions, QA pairs, questionnaire
```

1. Python 3.11: `pip install -r lufy/requirements.txt -r requirements-study.txt`.
2. `python -m study.admin links --base https://<mac>.<tailnet>.ts.net` gives every
   participant a token and prints their links.
3. `scripts/run_all.sh` starts both engines and the portal.
4. `tailscale funnel --bg 8000` publishes the portal (only the portal).
5. Keep the Mac awake (`caffeinate -dims &` or System Settings).
6. Progress: `python -m study.progress --logs runs/logs`.

Note: `lufy/roberta/inference_large.py` sets `CUDA_VISIBLE_DEVICES=4` (the
authors' server). On a one-GPU machine that hides the GPU, so RoBERTa runs on
CPU. It only runs in post-processing, so this costs time, not correctness.

## Tests

`python -m pytest -q tests` — stubs replace llama_index/OpenAI; no keys needed.

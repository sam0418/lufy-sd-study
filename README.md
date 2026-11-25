# LUFY + self-disclosure: user study system

Private. Unpublished research — do not push to a public remote.

Two LINE bots, one per condition, both running the original LUFY
(Sumida, Inoue & Kawahara, D&D 2025):

| Bot | Condition | Forgetting |
|-----|-----------|------------|
| A   | `lufy`    | original `forget_mechanism_wisely` |
| B   | `lufy_sd` | `forget_mechanism_sd_first`: self-disclosure memories are kept first; how many memories are kept and every survival probability are unchanged |

The bot-to-condition mapping lives only in each bot's `.env`; participants see
two bots, never a condition name. Swap the two `.env` files to change it.

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
- `study/line_bot.py` — LINE webhook. **Reply API only, never push**: no message
  quota is used, so the bot never speaks first and does not notify on idle close.

## Participant flow

1. Add both bots as LINE friends. Send `join P01` to each.
2. Send `start` → LUFY opens (session 1: asks for a self-introduction).
3. Chat. After 30 min LUFY appends a reminder once; the session keeps going.
4. Send `stop` → session saved. Post-processing (IPE, summary, SD tags,
   forgetting) runs in the background; `start` is refused until it finishes.
5. Sessions idle for 20 min are closed silently.

`stop`/`start` also accept 結束/開始 and ストップ/スタート.

## Setup

```
runs/
  participants.json      # copy participants.example.json; names letters only
  logs/                  # bot_A.jsonl, bot_B.jsonl (every turn, with latency)
  A/.env                 # copy A/.env.example
  B/.env
```

1. LINE Developers: one provider, two Messaging API channels (e.g. "LUFY A",
   "LUFY B"). Turn off auto-reply and greeting messages; turn on webhooks.
2. Python 3.11: `pip install -r lufy/requirements.txt -r requirements-study.txt`.
3. Run each bot from its own directory (memories go to `./memories`):

   ```
   cd runs/A && PYTHONPATH=../.. python -m study.line_bot
   cd runs/B && PYTHONPATH=../.. python -m study.line_bot
   ```
4. Expose the ports, e.g. `cloudflared tunnel --url http://localhost:8001`
   (and 8002), and set `https://<tunnel>/callback` as each channel's webhook URL.
5. Progress: `python -m study.progress --logs runs/logs`.

Note: `lufy/roberta/inference_large.py` sets `CUDA_VISIBLE_DEVICES=4` (the
authors' server). On a one-GPU machine that hides the GPU, so RoBERTa runs on
CPU. It only runs in post-processing, so this costs time, not correctness.

## Tests

`python -m pytest -q tests` — stubs replace llama_index/OpenAI; no keys needed.

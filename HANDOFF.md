# Running the demo on another laptop

Three things go wrong on a fresh machine. They are all fixable in
minutes, but only if you know about them before the client is watching.

---

## 1. `.env` carries live credentials — send it separately

The zip must **not** be the way these travel. `.env` contains:

| Value | What it is |
|---|---|
| `MONGODB_URI` | login to the real Zatch customer database |
| `AZURE_OPENAI_API_KEY` | billable Azure key |
| `JWT_SECRET` | signs tokens that grant access to any customer's data |
| `DEMO_MONGODB_URI` | writable demo cluster |
| `REDIS_URL` | password to the Upstash session/rate-limit store |

Sent over WhatsApp, email or a file-share, those land in a third party's
systems permanently. Send the `.env` **contents** over something you
control and would be willing to rotate afterwards, or hand them over in
person.

If in doubt: rotate the Azure key and the demo cluster password after
the demo. Both take a minute.

---

## 2. MongoDB Atlas will refuse his laptop

Atlas only accepts connections from allow-listed IP addresses. His IP is
not on the list, so `inspect_data.py` will fail with something that
*reads* like an auth error:

```
bad auth : authentication failed
```

That message is misleading — the credential may be perfect. Add his IP:

**Atlas → Network Access → Add IP Address → Add Current IP Address**
(he must do this from his own machine, or you add the address he tells
you).

Do this **before** demo day. It is the single most likely thing to
derail the handover.

---

## 3. Redis is hosted, so it travels with the project

`REDIS_URL` points at an **Upstash** database over TLS
(`rediss://...giving-mouse-179550.upstash.io:6379`), not at anything
running on the original laptop. It works from any machine with outbound
internet, so there is nothing to install and nothing to start.

Two things depend on it, and both used to break silently without it:

- **conversation memory** — survives restarts and is shared across
  workers, so `--reload` and mid-demo restarts are no longer dangerous
- **the `/chat` rate limit** — counted in Redis, so the configured
  allowance is the real one no matter how many workers run

Verify it in one command before the demo:

```powershell
uv run python scripts/check_redis.py
```

It does a real write/read round trip. If it cannot connect, the app
still runs — it degrades to per-process state and logs
`redis_unavailable` — so this is a "fix before deploying", not a
"stop the demo".

**Why Upstash and not Redis Cloud.** Redis Cloud's free 30MB plan does
not allow TLS, and session history holds real order IDs, tracking
numbers and delivery cities. Upstash is TLS-only on its free tier, so
that data is encrypted in transit. If anyone moves this to another
provider, keep the `rediss://` scheme — `redis://` to a remote host
sends real customer data in the clear, and `check_redis.py` will say so.

---

## Setup on his machine

**Do not zip `.venv/`** — it is large and machine-specific. Also skip
`__pycache__/` and `.pytest_cache/`.

```powershell
# 1. Install uv (once)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. In the unzipped folder - recreates the environment from uv.lock
uv sync

# 3. Put .env in the folder root (sent separately - see section 1)

# 4. Verify, in this order
uv run python scripts/inspect_data.py     # data + connection
uv run python scripts/check_azure.py      # model + credit remaining
uv run pytest -q                          # 374 should pass
```

If `inspect_data.py` fails, it is almost certainly section 2.

---

## Running the demo

```powershell
uv run python scripts/warm_cache.py            # makes the first answer fast
uv run python scripts/generate_test_token.py   # copy the eyJ... line
uv run uvicorn app.api.main:app                # NO --reload
```

Then open **http://127.0.0.1:8000/demo**, paste the token, and click
**Start chatting**.

The page opens with six starter questions as tappable chips. They are
worth using rather than typing: every one leans on the signed-in user's
own records or a general listing, so none of them can miss because a
particular product is absent from the dataset. Under each answer it
shows the time to the first word and which sources it checked - that
line is the easiest way to make the "it reads real data" point without
saying it.

Questions to ask are in `docs/demo-script.md`, along with the answers
they produced on the last rehearsal.

---

## Rehearse once on his machine

```powershell
$env:PAUSE_SECONDS=0; uv run python scripts/rehearse_demo.py --real-data
```

~90 seconds, and it now drives the SAME streaming path the demo page
uses, so a pass means the thing the client will watch actually works.
Each question reports how many tokens streamed and which lookup ran.

**Run it once, and not twice in a row.** One rehearsal spends about
61,000 prompt tokens and Azure allows 100,000 a minute, so a second run
inside the same minute runs out part-way through and reports the last
questions as failures. That is the quota, not the code - the failure
line now says so. Wait a minute if you need to repeat it, and leave a
minute between the rehearsal and the demo itself.

It must end with:

```
All questions answered, and every answer actually mentioned what it was asked about.
```

If it does not, do not demo until it does — the failure message names
the question and the reason.

---

## Two things to say out loud during the demo

**The security question** returns *"I couldn't find any order with that
ID"*, which looks like a bug unless narrated:

> That order ID is real — it came from your database, and it belongs to a
> different customer. The assistant isn't declining to show it; the query
> is scoped by the login token, so the database never returned it.

**The prices look odd** — ₹10 products, ₹30 order totals. That is the
real staging data. Say so before anyone asks.

---

## Known limits, so he is not surprised

- Answers take **~8 seconds** for a single lookup and **~17 seconds**
  when the assistant needs two rounds ("where is my order and can I
  cancel it?"). The wait is no longer blank: `/demo` names the lookup
  in progress within about 5 seconds, then types the answer out word by
  word as the model writes it. The timing under each reply shows both
  numbers — time to first word, and total.

  You no longer have to talk over it, but the two-round questions are
  still worth narrating.
- Free-text semantic search (*"something warm to wear"*) is **not**
  available on real data and will politely refuse. `anything similar to X`
  works and is the one to demo.
- The assistant is **read-only**. Asked to place an offer or add to cart,
  it points at the app. That is correct behaviour, not a limitation to
  apologise for.
- The Azure credit expires **2026-09-25** — see the runbook below.

---

## When the Azure credit runs out (2026-09-25)

While `MONGODB_DATABASE=zatch` (real customers), Groq and Gemini are
dropped from the provider chain automatically — their terms permit
training on prompts, and those prompts contain real orders. That leaves
Azure alone, so when its credit lapses every request answers *"I can't
reach my assistant service at the moment."*

**You will not be surprised by it.** The service now says so itself:

- at startup, a `llm_chain_preflight` log line — `warning` while the
  chain is one deep, `error` once the credit has lapsed
- `/health` reports `"llm": {"status": ..., "redundant": false}`;
  `unavailable` there degrades the overall status, which is what
  monitoring should alert on
- `uv run python scripts/check_azure.py` prints the days remaining and
  the fixes below

### The three fixes, best first

**1. Renew the credit.** Nothing else to change.

**2. Point `BACKUP_LLM_*` at another compliant provider.** This is a
`.env` change and a restart — no code change, which is the whole reason
those settings exist. Any OpenAI-compatible endpoint works (OpenAI's own
API, a second Azure resource, AWS Bedrock, Google Vertex):

```
BACKUP_LLM_BASE_URL=https://api.example.com/v1
BACKUP_LLM_API_KEY=...
BACKUP_LLM_MODEL=...
BACKUP_LLM_TRAINS_ON_PROMPTS=false
```

That last line is a claim about **someone else's contract**. Set it to
`false` only if their terms actually say submitted content is not used
for training. Leave it alone and the backup is treated exactly like Groq
— dropped the moment real customer data is configured, so it will not
help. Set it wrongly and real orders go somewhere that may learn from
them. Check the terms; it is a two-minute read and it is the whole
safety property.

Confirm it took effect — the chain should be two deep:

```bash
uv run python scripts/check_azure.py
```

**3. Demo only, same day: `MONGODB_DATABASE=zatch_demo`.** The filter is
keyed on the database, so pointing at the demo dataset re-enables Groq
and Gemini automatically and the assistant works again immediately. The
answers come from seeded data rather than real orders — fine for showing
the product, not for answering a real customer's question.

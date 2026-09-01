# Zatch Unified Conversational AI Assistant — Full Implementation Plan

**Status:** Planning document only — no code written yet.
**Starting point:** Empty project folder.
**End point:** Chatbot live inside the Zatch mobile app on Play Store.

This plan is organized as **Phase → Step → Sub-step**. Every step tells you
**WHAT** we're doing, **WHY**, how it **FLOWS** from the step before/after it,
and what **CHECKPOINT** (result you should see) tells you it's done correctly.

Feature build order follows the PDF's own weighting: Order Management and
Product Discovery are the most detailed/most-used sections, Bargaining is
called out as Zatch's "signature feature," and Fallback/General is built
early (rough) and polished late, because good fallback behavior makes every
other phase safer to test.

---

## PHASE 0 — Environment & Tooling Setup

**Goal:** Get a working, reproducible development environment before writing
a single line of application logic.

### Step 0.1 — Install `uv`
- **What:** Install the `uv` Python package/project manager on your machine.
- **Why:** `uv` locks exact dependency versions into a lockfile, so what
  works on your laptop is *guaranteed* to work on the server. This avoids
  the classic "works on my machine" production bug.
- **Flow:** This is a one-time, global, OS-level install — happens before
  any project exists.
- **Checkpoint:** Running `uv --version` in a terminal prints a version
  number.

### Step 0.2 — Initialize the project
- **What:** Turn the empty folder into a `uv`-managed Python project
  (creates `pyproject.toml`, a virtual environment, a lockfile).
- **Why:** `pyproject.toml` becomes the single source of truth for what
  Python version and packages this project needs — critical for a real
  production deploy later.
- **Flow:** Comes right after `uv` is installed; every dependency we add in
  later phases will be added *into* this project.
- **Checkpoint:** Folder contains `pyproject.toml`, `.venv/`, and a lockfile;
  `uv run python --version` shows a pinned Python version.

### Step 0.3 — Set up git
- **What:** Initialize a git repository and a sensible `.gitignore`.
- **Why:** Version control is non-negotiable for production software — you
  need history, rollback ability, and (later) code review before deploy.
- **Flow:** Runs alongside project init; every phase from here on is a
  sequence of commits.
- **Checkpoint:** `git status` works inside the folder; `.venv`, `.env`, and
  cache folders are git-ignored (so secrets and bulky files never get
  committed).

### Step 0.4 — Set up secrets handling
- **What:** Create a `.env` file (never committed) to hold the MongoDB
  connection string, LLM API key, JWT secret, etc., plus an
  `.env.example` (committed) showing which variables are needed without
  real values.
- **Why:** Hardcoding secrets in code is one of the most common causes of
  production data breaches — secrets belong in environment variables, kept
  out of git entirely.
- **Flow:** This convention is used by every later phase that needs a
  credential (DB connection, LLM key, etc.).
- **Checkpoint:** `.env` exists locally with placeholder values, is listed
  in `.gitignore`, and `.env.example` is committed with empty/fake values.

---

## PHASE 1 — Project Foundation & Configuration

### Step 1.1 — Define the folder structure
- **What:** Lay out folders such as `app/config`, `app/db`, `app/repos`,
  `app/features`, `app/agent`, `app/api`, `app/tests` before any code
  exists in them.
- **Why:** A clear structure from day one prevents the common beginner
  problem of "one giant file that does everything," which becomes
  unmaintainable and unsafe to change once live.
- **Flow:** Every later phase adds files into one of these folders — this
  step just draws the map.
- **Checkpoint:** Folder tree exists (even if mostly empty) and matches the
  plan; you can explain out loud what each folder is for.

### Step 1.2 — Build the settings/config loader
- **What:** One central place in code that reads all environment variables
  (DB URI, API keys, etc.) and validates they're present at startup.
- **Why:** If a secret is missing, the app should fail loudly and
  immediately at startup — not silently fail later mid-conversation with a
  real user.
- **Flow:** Every other module (DB layer, LLM layer, API layer) will import
  settings from here rather than reading `os.environ` directly.
- **Checkpoint:** Running the app with a missing `.env` variable produces a
  clear startup error naming the missing variable.

### Step 1.3 — Set up structured logging
- **What:** A logging setup that outputs structured (JSON-friendly) logs
  and has a built-in rule: certain field names (password, token, phone,
  email, bank details) are automatically redacted if they ever appear in a
  log call.
- **Why:** In real incidents, engineers grep logs — if logs accidentally
  contain sensitive data, that data leaks through log files/dashboards.
  Building the redaction habit now avoids retrofitting it after a leak.
- **Flow:** Every phase from here on uses this logger instead of
  `print()`.
- **Checkpoint:** A test log call containing a fake "password" field shows
  up in the log as redacted (e.g., `***`), not in plaintext.

---

## PHASE 2 — Database Connection Layer (Read-Only MongoDB Atlas)

### Step 2.1 — Confirm the read-only DB user
- **What:** Verify with whoever manages Atlas that the chatbot's database
  user has **read-only** role at the database level (not just "we'll be
  careful in code").
- **Why:** Enforcing read-only at the database/infrastructure level means
  even a bug in our code *physically cannot* write, update, or delete data.
  Code-level promises can be bypassed by a bug; database-level permissions
  cannot.
- **Flow:** This is a prerequisite before writing any connection code —
  everything downstream assumes this is already true.
- **Checkpoint:** You (or your DB admin) can show the Atlas user's role is
  `read` only on the `zatch` database, and a manual write attempt with
  those credentials is rejected by MongoDB itself.

### Step 2.2 — Add the async MongoDB driver
- **What:** Add `motor` (the async MongoDB driver for Python) as a project
  dependency via `uv`.
- **Why:** A chatbot serving many users at once needs non-blocking
  (async) database calls, or one slow query freezes every other user's
  conversation.
- **Flow:** This becomes the foundation every repository function (Phase 4)
  is built on.
- **Checkpoint:** The dependency appears in `pyproject.toml` and the
  lockfile after running the add command.

### Step 2.3 — Build the connection module
- **What:** One module that opens a single reused connection ("client")
  to MongoDB Atlas at app startup, using the connection string from
  settings.
- **Why:** Reconnecting to the database on every single chat message is
  slow and wasteful; a connection pool shared across requests is standard
  production practice.
- **Flow:** All repository functions (Phase 4) will pull their database
  handle from this one module rather than creating their own connections.
- **Checkpoint:** A simple startup check successfully connects and can
  list the 16 known collection names from the live database.

### Step 2.4 — Add a health check
- **What:** A small function/endpoint that pings the database and reports
  "connected" or "disconnected."
- **Why:** In production you need an automated way to know the DB
  connection is alive *before* a real user hits an error — this becomes
  part of your monitoring in Phase 13.
- **Flow:** Used immediately in local development, later reused by the
  cloud host's health-check system in Phase 11.
- **Checkpoint:** Calling the health check locally returns "connected."

---

## PHASE 3 — Security Foundation: Auth Context & Field Protection

This phase is placed early, before feature logic, on purpose: every feature
in Phase 5 depends on these protections existing first.

### Step 3.1 — Design the auth-context flow
- **What:** Decide how the chatbot learns *who* is chatting. The Zatch
  mobile app already logs users in — the plan is for the app to send a
  verified JWT (the same one used elsewhere in Zatch) with each chat
  request; our backend decodes it server-side to get `buyerId`/`sellerId`.
- **Why:** We must **never** trust a user ID typed or sent in the chat
  message body itself — a malicious or buggy client could send someone
  else's ID and see their orders. The ID must come from a cryptographically
  verified token, not free-form input.
- **Flow:** This decision shapes every repository function in Phase 4 —
  each one will require a verified `user_id` as an argument.
- **Checkpoint:** You can clearly state, in one sentence, where `buyerId`/
  `sellerId` comes from and why the chat text itself is never trusted for
  identity.

### Step 3.2 — Build JWT verification
- **What:** A module that verifies the token's signature and expiry, and
  extracts the user ID — rejecting anything invalid or expired.
- **Why:** This is what actually enforces Step 3.1's design — without
  signature verification, anyone could fabricate a token claiming to be
  another user.
- **Flow:** Sits in front of every API call in Phase 9 — no request reaches
  feature logic without passing this check first.
- **Checkpoint:** A valid test token is accepted and yields the correct
  user ID; a tampered/expired token is rejected with a clear error.

### Step 3.3 — Define the sensitive-field allowlist
- **What:** A single, shared list (or per-collection allowlist) declaring
  exactly which fields are allowed to leave the database layer —
  explicitly excluding `password`, `refreshToken`, `bankDetails`,
  `payoutDestination`, `phone`, `email` (per your constraints), among any
  others you judge sensitive.
- **Why:** An "allowlist" (only listed fields pass through) is safer than
  a "blocklist" (block known-bad fields) — if a new sensitive field is
  added to the database later, an allowlist keeps it hidden by default; a
  blocklist would leak it by default until someone remembers to add it.
- **Flow:** Every repository function in Phase 4 uses this allowlist when
  shaping its MongoDB projection (i.e., which fields to fetch at all).
- **Checkpoint:** You have a written list per collection of exactly which
  fields the chatbot is allowed to read and return.

### Step 3.4 — Build a response sanitizer (defense in depth)
- **What:** A final safety-net function that strips any disallowed field
  from a response right before it's sent back — even if a repository
  function had a bug and fetched too much.
- **Why:** "Defense in depth" means security doesn't rely on one perfect
  layer; if Phase 3.3's allowlist has a mistake somewhere, this catches it
  anyway before data reaches the user.
- **Flow:** Sits at the very end of the pipeline, just before Phase 9's API
  layer sends a response.
- **Checkpoint:** A deliberately "broken" test repo function that fetches a
  forbidden field (e.g., `password`) still results in a clean response
  with that field stripped.

---

## PHASE 4 — Data Access Layer (Repository Pattern)

**Goal:** One well-tested, safe function per data need — nothing in later
phases ever queries MongoDB directly.

### Step 4.1 — Establish the repository pattern
- **What:** Decide the convention: one file per collection (e.g.
  `orders_repo.py`, `products_repo.py`), each function always takes
  `user_id` as a required argument and always applies the Phase 3
  allowlist.
- **Why:** Centralizing all database access this way means "scope every
  query to the logged-in user" (your constraint) is enforced structurally,
  not just remembered by habit — it's much harder to accidentally forget
  a scoping filter when the function signature demands a `user_id`.
- **Flow:** Feature logic (Phase 5) will only ever call these repo
  functions, never touch MongoDB directly.
- **Checkpoint:** You can point to the exact file/function that would
  answer "what's in my cart?" before any feature logic is built.

### Step 4.2 — Build repos in priority order (matching PDF's usage weight)
Each sub-step below is its own small unit of work:
  - 4.2.1 `orders_repo` — status, delivery date, history, item/pricing
    detail, invoice link, tracking, cancellation-eligibility check
  - 4.2.2 `products_repo` — category/subcategory search, price filtering,
    variant/stock lookup, product detail, trending/top-pick, seller lookup
  - 4.2.3 `bargains_repo` — eligibility, live status, counter-offer,
    suggested offer calculation
  - 4.2.4 `carts_repo` + `coupons_repo` — cart contents/total, read-only
    coupon validity check
  - 4.2.5 `livesessions_repo` — live-now check, product sequence, recap
  - 4.2.6 `bits_repo` — trending, tagged products, hashtag search
  - 4.2.7 `reviews_repo` — rating/comment summary per product
  - 4.2.8 `users_repo` (safe subset) + `addresses_repo` +
    `notifications_repo` — default address, unread notification summary,
    saved items, followers/following
  - 4.2.9 `payouts_repo` + seller-side slices of `users_repo` — payout
    status, sales performance, coupon performance, pending bargain count
- **Why this order:** Matches the PDF's own emphasis — Order Management and
  Product Discovery are the most-detailed, most-likely-used sections;
  Bargaining is explicitly the "signature feature"; seller-side tools are
  used less often (only by sellers) so they come later.
- **Flow:** Each repo is independently testable in isolation before the LLM
  ever touches it — this makes Phase 10 (testing) far easier.
- **Checkpoint:** For each repo, you can run a manual test call with a real
  test user ID and get back correct, properly-scoped, sensitive-field-free
  data.

### Step 4.3 — Handle the "empty result" case everywhere
- **What:** Every repo function has a defined, graceful behavior for "no
  data found" (e.g., no orders yet) rather than throwing an unhandled
  error.
- **Why:** This is explicitly required by the PDF's non-functional
  requirements (graceful error handling) — a chatbot that crashes on an
  empty cart looks broken to a real user.
- **Flow:** Feeds directly into Phase 5.10 (General & Fallback logic).
- **Checkpoint:** Calling any repo function for a brand-new test user with
  no data returns a clean "empty" result, not a stack trace.

---

## PHASE 5 — Feature Logic (Grouped by PDF Feature Areas, Priority Order)

**Goal:** Turn each repo function into a well-defined "capability" the
chatbot can offer, with the exact response shape and edge cases decided.

For each of the 10 areas below, the process is the same: **(a)** decide the
exact question forms it should handle, **(b)** decide the exact response
format, **(c)** identify edge cases (not found, ambiguous — e.g. "my order"
when the user has 5 active orders), **(d)** write down 2–3 example
conversations to test against later.

### Step 5.1 — Order Management (PDF §3)
- **What:** Status tracking, delivery estimate, order history, order detail,
  invoice link, shipment tracking, cancellation-eligibility check.
- **Why first:** Highest-detail, most-used section in the PDF; buyers check
  order status far more than anything else.
- **Flow:** Built directly on `orders_repo` from Phase 4.2.1.
- **Checkpoint:** Written spec for each of the 7 sub-features listing exact
  input → output shape, ready to hand to Phase 6/7's agent layer.

### Step 5.2 — Product Discovery (PDF §4)
- **What:** Category search, price filtering, variant/stock check, product
  detail, trending, personalized recommendations, seller identification.
- **Flow:** Built on `products_repo`; this is also where natural-language
  search planning happens (see Phase 5.2.1 below).

  #### Step 5.2.1 — Natural language → structured filter design
  - **What:** Decide the exact set of filter fields the system understands
    (category, subCategory, min/max price, color, size, in-stock only,
    etc.) and how free text like "shirts under 500" maps onto them.
  - **Why:** Rather than writing a fragile hand-built text parser, we plan
    to let the LLM itself extract these structured filters (detailed in
    Phase 6/7) — but the *filter schema itself* must be designed first,
    independent of which LLM we use.
  - **Checkpoint:** A written table of supported filter fields and 5+
    example phrases mapped by hand to the filters they should produce.

### Step 5.3 — Bargaining (PDF §5 — signature feature)
- **What:** Explaining the feature, eligibility check, live offer status,
  counter-offer display, suggested offer amount.
- **Why here:** Explicitly Zatch's differentiator per the PDF; comes right
  after core browsing/ordering needs.
- **Flow:** Built on `bargains_repo` + `products_repo.bargainSettings`.
- **Checkpoint:** Written spec covering all 5 sub-features plus the FAQ
  answer for "what is bargaining."

### Step 5.4 — Cart & Checkout, read-only (PDF §6)
- **What:** Cart contents, cart total, coupon validity check (**never**
  applies the coupon — only reports if it *would* work).
- **Flow:** Built on `carts_repo` + `coupons_repo`.
- **Checkpoint:** Spec confirms the bot's coupon response always ends by
  directing the user to apply it themselves at checkout (matches the
  read-only constraint).

### Step 5.5 — Live Shopping (PDF §7)
- **What:** Is anyone live now, what's being shown, recap of a past
  session.
- **Flow:** Built on `livesessions_repo`.
- **Checkpoint:** Spec written for all 3 sub-features.

### Step 5.6 — Bits / Short Video (PDF §8)
- **What:** Trending Bits, tagged products in a video, hashtag search.
- **Flow:** Built on `bits_repo`.
- **Checkpoint:** Spec written for all 3 sub-features.

### Step 5.7 — Reviews & Trust (PDF §9)
- **What:** Review/rating summary, seller trust indicators.
- **Flow:** Built on `reviews_repo` + safe subset of `users_repo`.
- **Checkpoint:** Spec written; note this is a summarization task, good
  candidate to test LLM quality on.

### Step 5.8 — Account & Profile (PDF §10)
- **What:** Default address, unread notification summary, saved items,
  followers/following.
- **Flow:** Built on `addresses_repo`, `notifications_repo`,
  `users_repo` safe subset.
- **Checkpoint:** Spec written for all 4 sub-features.

### Step 5.9 — Seller-Side Support (PDF §11)
- **What:** Payout status, sales performance summary, coupon performance,
  pending bargain requests.
- **Why later:** Only relevant to users who are sellers — smaller audience
  than universal buyer features above.
- **Flow:** Built on `payouts_repo`, `coupons_repo`, `bargains_repo`,
  seller subset of `users_repo`.
- **Checkpoint:** Spec written for all 4 sub-features, plus a rule for
  detecting "is this user a seller" before offering these.

### Step 5.10 — General & Fallback (PDF §12)
- **What:** Policy/FAQ answers, and a graceful fallback for anything the
  bot can't resolve (e.g., payment gateway issues → redirect to human
  support, since the bot has no write access to create tickets).
- **Why built early in rough form, polished last:** A working fallback from
  day one makes every other phase's manual testing safer — an
  unrecognized query fails gracefully instead of confusingly.
- **Flow:** This becomes the default branch in Phase 6/7's routing logic.
- **Checkpoint:** Spec confirms the bot never pretends to do something it
  can't (like send a reminder or file a ticket) — it says so plainly and
  redirects.

---

## PHASE 6 — Choosing and Setting Up the LLM

### Step 6.1 — Decide the model
- **What:** Choose an LLM provider/model for both (a) understanding user
  intent and extracting structured filters, and (b) generating natural
  final replies.
- **Why an LLM at all, rather than hand-written rules:** Real users phrase
  the same request many different ways ("where's my stuff," "track order,"
  "when's my package coming") — an LLM generalizes across phrasing far
  better than a rule-based parser, and it can hold multi-turn context
  (Phase 8) naturally.
- **Flow:** This choice affects Phase 7's tool-calling design, so it's
  decided before building the agent layer.
- **Checkpoint:** Written decision + reasoning (cost, latency, tool-calling
  quality) recorded, with your API key obtained and stored per Step 0.4.

### Step 6.2 — Set up the API client
- **What:** Add the LLM's SDK as a dependency, wire it to read the API key
  from settings (Phase 1.2), and do a minimal "hello world" call.
- **Why:** Confirms connectivity and credential correctness in isolation,
  before wiring anything complex to it.
- **Checkpoint:** A test script sends one message and prints back a real
  model response.

---

## PHASE 7 — Agent / Tool-Calling Layer

### Step 7.1 — Turn each Phase 5 capability into a "tool"
- **What:** Wrap each repo/feature function (order status, product search,
  bargain status, etc.) as a formally described "tool" the LLM can choose
  to call, each with a name, description, and expected parameters.
- **Why:** Tool-calling lets the LLM decide *which* function answers a
  given question and *what parameters* to pass (e.g., turning "shirts
  under 500" into `{category: "shirts", max_price: 500}`) — this is the
  standard, safest way to connect an LLM to real backend logic, since the
  LLM never touches the database directly, only calls pre-approved
  functions.
- **Flow:** Depends on Phase 5's specs (what each feature needs as input)
  and Phase 6's chosen model (needs to support tool-calling).
- **Checkpoint:** A written tool definition exists for every Phase 5
  sub-feature, each mapped to its exact repo function.

### Step 7.2 — Build the orchestration loop
- **What:** The core loop: user message → LLM decides which tool(s) to call
  → our code executes the tool (safely, scoped to `user_id` from Phase 3)
  → result goes back to the LLM → LLM writes the final natural-language
  reply.
- **Why:** This is the "brain" that connects everything built so far into
  one working assistant.
- **Flow:** Sits directly on top of Phase 4 (repos), Phase 3 (security),
  and Phase 6 (LLM client).
- **Checkpoint:** A manual test: typing "where is my order" as a known test
  user produces a correct, natural-sounding reply pulling real data from
  the live database.

### Step 7.3 — Enforce user-scoping inside the loop
- **What:** The orchestration loop injects the verified `user_id` (from
  Phase 3.2) into every tool call itself — it is never something the LLM
  is asked to supply or the user can influence via chat text.
- **Why:** This is the concrete mechanism that guarantees "never leak
  another user's data," restated from Phase 3.1 but now actually wired
  into the execution path.
- **Checkpoint:** A test where the chat message tries to reference another
  user's order ID directly still only returns results scoped to the
  authenticated user.

---

## PHASE 8 — Conversation Memory (Multi-turn Context)

### Step 8.1 — Decide memory scope and storage
- **What:** Decide what "remembering" means here — within a single active
  conversation session, the bot recalls which product/order was discussed
  earlier, so "is it in stock?" after asking about a jacket knows which
  jacket. Decide where this short-term conversation history is held (e.g.
  in-memory per session, or a lightweight session store) — noting we are
  **not** writing anything to MongoDB.
- **Why:** The PDF's non-functional requirements explicitly call out
  multi-turn context as a requirement; without it, every message feels
  disconnected and the user has to over-repeat themselves.
- **Flow:** Wraps around Phase 7's orchestration loop — each call includes
  recent conversation history as input to the LLM.
- **Checkpoint:** A manual two-message test: "tell me about the blue
  jacket" then "is it in stock?" correctly resolves "it" to the jacket
  from the previous message.

### Step 8.2 — Define memory limits and expiry
- **What:** Decide how much history is kept and for how long (e.g., last N
  messages, or session timeout after inactivity).
- **Why:** Unbounded memory grows the LLM's request size (cost + latency)
  over a long conversation, and old context can become irrelevant or
  confusing.
- **Checkpoint:** A long test conversation confirms older, irrelevant
  context is dropped without breaking recent context.

---

## PHASE 9 — Backend API Layer

### Step 9.1 — Choose and set up the web framework
- **What:** Add FastAPI (or similar async-friendly framework) as a
  dependency and set up the minimal app skeleton.
- **Why:** FastAPI pairs naturally with the async `motor` driver from
  Phase 2, has built-in request validation, and produces auto-generated
  API docs — useful when your mobile team integrates in Phase 12.
- **Checkpoint:** Running the dev server locally shows a working root
  endpoint and interactive API docs page.

### Step 9.2 — Build the `/chat` endpoint
- **What:** One endpoint that accepts the verified JWT (Phase 3.2) and the
  user's message, runs it through Phase 7's orchestration loop plus Phase
  8's memory, and returns the reply.
- **Why:** This is the single integration point the mobile app will call —
  everything before this phase was internal plumbing.
- **Flow:** Sits directly on top of Phases 3, 7, and 8.
- **Checkpoint:** Sending a test HTTP request with a valid token and a
  message like "where is my order" returns a correct JSON response.

### Step 9.3 — Add request/response validation
- **What:** Strict schemas for what a request must contain and what a
  response will always look like.
- **Why:** Prevents malformed input from causing confusing errors deep in
  the system, and gives the mobile team a stable contract to build
  against.
- **Checkpoint:** Sending a deliberately malformed request returns a clear
  400-style error, not a crash.

### Step 9.4 — Add streaming (optional but recommended)
- **What:** Stream the reply back token-by-token instead of waiting for
  the full response.
- **Why:** Matches the PDF's "response latency" non-functional requirement
  — perceived speed improves a lot even if total generation time is
  similar, which matters a lot on mobile.
- **Checkpoint:** A test client shows text appearing incrementally rather
  than all at once.

---

## PHASE 10 — Testing

### Step 10.1 — Unit test every repository function
- **What:** For each Phase 4 repo function, test: correct data returned,
  correct user-scoping, correct field-allowlisting, correct empty-result
  handling.
- **Why:** These are the most security-critical and most reused pieces —
  bugs here affect every feature built on top.
- **Checkpoint:** A test suite runs (via `uv run pytest` or similar) and
  passes for every repo.

### Step 10.2 — Integration test the full chat flow
- **What:** Test realistic full conversations end-to-end through the
  `/chat` endpoint, including the multi-turn examples from Phase 5's
  specs.
- **Why:** Confirms the LLM's tool selection, the orchestration loop, and
  memory all work together — not just each piece in isolation.
- **Checkpoint:** A written list of test conversations (one or more per
  PDF sub-feature) all pass with correct, safe answers.

### Step 10.3 — Security-focused testing
- **What:** Deliberately try to break scoping — request another user's
  order by ID in chat text, send an expired/tampered token, ask questions
  designed to leak sensitive fields.
- **Why:** This directly tests your hard constraints (read-only, no
  cross-user leaks, no sensitive fields) under adversarial conditions, not
  just happy-path use.
- **Checkpoint:** Every attempted violation is blocked and logged, not
  silently succeeding.

### Step 10.4 — Load/latency testing
- **What:** Simulate multiple concurrent users chatting at once.
- **Why:** Confirms the async design (Phase 2, Phase 9) actually holds up
  under real concurrent load before real users hit it.
- **Checkpoint:** Response times stay acceptable under a realistic
  concurrent load test.

---

## PHASE 11 — Deployment to a Live Cloud Environment

### Step 11.1 — Containerize the app
- **What:** Write a Dockerfile that builds the app using `uv` (so the
  container gets the exact locked dependencies from Phase 0.2).
- **Why:** Containers guarantee the production environment matches what
  you tested locally — eliminating environment-mismatch bugs.
- **Checkpoint:** `docker build` succeeds and the container runs the app
  locally, reachable the same way the dev server was.

### Step 11.2 — Set up cloud secrets management
- **What:** Move `.env` values into your cloud provider's secrets manager
  rather than baking them into the container image.
- **Why:** Secrets baked into images can leak if the image is ever shared
  or leaked; a secrets manager keeps them separate and rotatable.
- **Checkpoint:** The deployed container starts successfully pulling
  secrets from the cloud, with no secret values present in the image
  itself.

### Step 11.3 — Deploy and configure health checks
- **What:** Deploy the container to your chosen host, wiring the health
  check from Step 2.4 into the platform's automated health monitoring.
- **Why:** Lets the platform automatically detect and restart an unhealthy
  instance before it affects real users.
- **Checkpoint:** The service is reachable at a public/internal URL and the
  platform dashboard shows it as "healthy."

### Step 11.4 — Set up staging vs. production environments
- **What:** Maintain two deployed copies — staging (for testing changes)
  and production (what the live app talks to).
- **Why:** Never test unproven changes directly against real users on a
  live Play Store app.
- **Checkpoint:** You can deploy a change to staging, verify it, and only
  then promote the same build to production.

---

## PHASE 12 — Integrating Into the Live Zatch Mobile App

### Step 12.1 — Define the mobile ↔ backend contract
- **What:** Formal documentation (from Phase 9.3's schemas) of exactly what
  the app must send and what it will get back, handed to your mobile team.
- **Why:** A stable, documented contract lets mobile and backend work in
  parallel without breaking each other.
- **Checkpoint:** Mobile team confirms they can construct a valid request
  using only the docs, without needing to ask you questions.

### Step 12.2 — Wire up the chat UI to the backend
- **What:** The mobile app's existing (or new) chat screen calls the
  `/chat` endpoint, attaching the user's existing login JWT automatically.
- **Why:** Reuses Zatch's existing auth system rather than inventing a new
  one — keeps the identity chain (Phase 3) intact end to end.
- **Checkpoint:** From a real (staging) build of the app, sending a chat
  message produces a correct reply on-device.

### Step 12.3 — Handle connectivity edge cases in the app
- **What:** App-side handling for slow responses, timeouts, and backend
  errors (showing a friendly retry state, not a crash).
- **Why:** Mobile networks are unreliable; the integration must degrade
  gracefully.
- **Checkpoint:** Manually simulating a dropped connection or slow response
  in staging shows a sensible in-app state, not a frozen or crashed UI.

### Step 12.4 — Staged rollout
- **What:** Release to a small percentage of Play Store users first (a
  phased/staged rollout, which Play Store supports natively), watching for
  issues before going to 100%.
- **Why:** Limits the blast radius of any unexpected production issue on a
  real, already-live app.
- **Checkpoint:** A defined rollout percentage schedule (e.g., 5% → 25% →
  100%) with clear go/no-go criteria at each stage.

---

## PHASE 13 — Monitoring, Logging, and Post-Launch Maintenance

### Step 13.1 — Centralize logs and errors
- **What:** Ship the structured logs from Step 1.3 to a central logging/
  error-tracking service.
- **Why:** You can't fix what you can't see — centralized logs let you
  spot real-user issues quickly after launch.
- **Checkpoint:** A deliberately triggered test error shows up in the
  central dashboard within a reasonable time.

### Step 13.2 — Track key metrics
- **What:** Track things like: chat response latency, LLM error rate, tool
  -call failure rate, fallback-triggered rate (how often the bot couldn't
  help).
- **Why:** These metrics tell you where users are getting stuck, guiding
  what to improve next — the fallback-rate metric in particular tells you
  which PDF features need better handling.
- **Checkpoint:** A dashboard exists showing these metrics updating from
  real traffic.

### Step 13.3 — Set up alerting
- **What:** Automated alerts (e.g., to Slack/email) when error rates or
  latency cross a threshold.
- **Why:** Ensures problems are caught within minutes, not discovered days
  later from user complaints.
- **Checkpoint:** A test-triggered threshold breach produces a real alert.

### Step 13.4 — Plan ongoing maintenance
- **What:** Recurring practices: reviewing fallback logs to find missing
  features, updating tool definitions as the PDF's requirements evolve,
  periodic security review of the field-allowlist, dependency updates via
  `uv`.
- **Why:** A production chatbot is never "done" — real usage surfaces gaps
  the original spec didn't anticipate.
- **Checkpoint:** A written, recurring maintenance checklist (weekly/
  monthly cadence) exists and has an owner.

---

## Summary — Order of Execution

```
Phase 0  → Tooling (uv, git, secrets)
Phase 1  → Project foundation (structure, config, logging)
Phase 2  → Read-only MongoDB connection
Phase 3  → Security foundation (auth context, field allowlist, sanitizer)
Phase 4  → Repository layer (Orders → Products → Bargains → Cart/Coupons →
           Live → Bits → Reviews → Account → Seller/Payouts)
Phase 5  → Feature specs, same priority order as Phase 4
Phase 6  → LLM selection & setup
Phase 7  → Tool-calling agent layer
Phase 8  → Conversation memory
Phase 9  → Backend API (/chat endpoint)
Phase 10 → Testing (unit → integration → security → load)
Phase 11 → Cloud deployment (staging + production)
Phase 12 → Mobile app integration (staged rollout)
Phase 13 → Monitoring & ongoing maintenance
```

**Next step, when you're ready:** We start executing at Phase 0, Step 0.1 —
installing `uv` — one command at a time, with your confirmation between each
one.
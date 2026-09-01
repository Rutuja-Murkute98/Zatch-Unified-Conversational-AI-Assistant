"""
WHAT:
    A single-page chat UI at /demo, served by this same app.

WHY IT EXISTS:
    /docs is a developer tool. A client watching JSON go into a text box
    and JSON come back sees plumbing, not a product - and spends the
    demo translating rather than reacting. The same assistant behind a
    chat bubble reads as the thing they would ship.

    Deliberately one file with no build step, no framework and no
    external requests: it is served from the app's own origin, so there
    is no CORS to configure and nothing to deploy separately. That rule
    is why there are no web fonts and no icon library here - a demo that
    breaks because a CDN is slow is worse than one that uses system
    fonts.

THREE THINGS IT DOES THAT ARE NOT DECORATION:

    A PHONE FRAME ON DESKTOP. This assistant ships inside the Zatch
    mobile app, and the answers are formatted for a chat bubble on a
    phone - the system prompt says so explicitly, and forbids markdown
    tables and headings for that reason. Shown full-width in a browser
    the output looks oddly terse; shown in a phone-shaped column it
    looks like the product. On an actual phone the frame disappears.

    STARTER QUESTIONS. The demo dataset is small - around a dozen
    products - so an empty text box invites a visitor to invent a query
    that legitimately finds nothing, and "I couldn't find that" reads as
    weakness even when it is the correct answer. The chips ask things
    that work against ANY dataset, because they lean on the signed-in
    user's own records and on general listings rather than naming
    products that may not exist.

    A "CHECKED" LINE UNDER EACH ANSWER. The status events already say
    which lookups ran; keeping them visible afterwards turns the central
    claim of this project - that it reads real data rather than inventing
    it - into something a client can see rather than something they have
    to be told.

WHAT IT DOES NOT DO:
    No credential storage beyond the browser tab, no history persistence
    of its own (the server already owns that, keyed by session id), and
    no styling framework. It is a demo surface, not a product front end.

    Message text is always written with textContent, never innerHTML.
    Model output is not trusted markup, and a product description is
    attacker-controllable - see the prompt-injection note in
    orchestrator.build_system_prompt.
"""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.db.connection import get_database
from app.security.auth import create_test_token

router = APIRouter()


class DemoLoginRequest(BaseModel):
    username: str


# DEV-ONLY, DEMO-ONLY. Mounted only when DEMO_UI_ENABLED is on (same
# gate as the /demo page itself - see create_app()), so it never exists
# in a production build.
#
# WHAT THIS INTENTIONALLY BYPASSES: real auth means "prove who you are
# with a signature only the real backend can produce." This endpoint
# instead mints a valid token for WHOEVER'S USERNAME YOU TYPE - it is a
# convenience for local testing against real usernames without running
# scripts/generate_test_token.py by hand, not a login. Anyone who can
# reach this endpoint can become any user by name alone. That is exactly
# why it lives behind the same flag that already warns
# "a chat page is served to anyone who can reach this service."
@router.post("/demo/login", include_in_schema=False)
async def demo_login(payload: DemoLoginRequest) -> dict:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Enter a username.")

    db = get_database()
    # Exact match, case-insensitive - "pavan" should find "Pavan" without
    # accidentally matching "Pavan Kumar" too.
    user = await db.users.find_one(
        {"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}},
        {"username": 1},
    )
    if user is None:
        raise HTTPException(
            status_code=404, detail=f"No user named '{username}' found."
        )

    user_id = str(user["_id"])
    token = create_test_token(user_id=user_id, expires_in_minutes=120)
    return {"token": token, "user_id": user_id, "username": user["username"]}


# DEV-ONLY, DEMO-ONLY, same DEMO_UI_ENABLED gate as everything above.
#
# WHY THIS EXISTS SEPARATELY FROM THE CHAT TOOLS. get_product_detail
# already exists and is already a chat tool - but reaching it means a
# full LLM round trip (6-8s) just to open a picture the user already
# tapped, and tool_executor's trimmer deliberately drops the image
# array before the model ever sees it (see _trim_product_detail) - the
# model has no way to hand images back even if asked to. The UI needs
# the RAW repo result directly, the same way tool_executor reads it for
# the product-card event, just for one product instead of a search hit.
#
# NO user_id is threaded through here, on purpose: products_repo's own
# docstring is explicit that products are public - any user can browse
# any product - so there is no per-user scoping to enforce, unlike every
# order/cart/account endpoint in this app.
@router.get("/demo/product/{product_id}", include_in_schema=False)
async def demo_product_detail(product_id: str) -> dict:
    from app.agent.tool_executor import to_json_safe
    from app.repos import products_repo

    detail = await products_repo.get_product_detail(product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    # Same conversion execute_tool applies before anything leaves this
    # process - raw Mongo documents carry ObjectId/datetime values that
    # neither JSON nor FastAPI's serializer know how to handle.
    return to_json_safe(detail)


PAGE = r"""
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Zatch Assistant</title>
<style>
  :root {
    --bg:#080a0f;
    --frame:#0e1118;
    --surface:#141824;
    --surface-2:#1a1f2d;
    --line:#232a3a;
    --line-soft:#1b2130;
    --text:#e9edf6;
    --muted:#8b94ab;
    --accent:#00d99a;
    --accent-dim:#00a878;
    --blue:#3b82f6;
    --danger:#ff6b7a;
    --radius:18px;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  /* THE hidden ATTRIBUTE ONLY SETS display:none AT THE LOWEST
     SPECIFICITY, so any rule below that gives an element a display of
     its own silently beats it. .gate, #log and .composer are all
     display:flex, so `hidden` did nothing to them and the token gate
     stayed on screen underneath the conversation. Restated with
     !important, which is what the attribute is supposed to mean. */
  [hidden] { display:none !important; }

  body {
    margin:0;
    background:var(--bg);
    color:var(--text);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
    -webkit-font-smoothing:antialiased;
    display:flex; align-items:center; justify-content:center;
    min-height:100dvh; padding:24px;
  }
  /* Ambient light behind the frame. Two soft radial washes rather than a
     flat panel on a flat page - it is what stops the whole thing reading
     as a form. */
  body::before {
    content:""; position:fixed; inset:0; pointer-events:none;
    background:
      radial-gradient(60ch 40ch at 20% 0%, rgba(0,217,154,.10), transparent 60%),
      radial-gradient(50ch 40ch at 90% 100%, rgba(59,130,246,.10), transparent 60%);
  }

  /* ---- the phone ---- */
  .device {
    position:relative; z-index:1;
    width:100%; max-width:430px; height:min(880px, calc(100dvh - 48px));
    background:var(--frame);
    border:1px solid var(--line);
    border-radius:38px;
    box-shadow:
      0 0 0 1px rgba(255,255,255,.03) inset,
      0 40px 80px -20px rgba(0,0,0,.8),
      0 0 60px -20px rgba(0,217,154,.15);
    display:flex; flex-direction:column; overflow:hidden;
  }

  /* ---- header ---- */
  .bar {
    display:flex; align-items:center; gap:11px;
    padding:18px 20px 14px;
    border-bottom:1px solid var(--line-soft);
    background:linear-gradient(180deg, rgba(255,255,255,.025), transparent);
  }
  .mark {
    width:34px; height:34px; border-radius:11px; flex:none;
    background:linear-gradient(135deg, var(--accent), var(--blue));
    display:grid; place-items:center;
    font-weight:800; font-size:16px; color:#06120e; letter-spacing:-.5px;
  }
  .who { display:flex; flex-direction:column; gap:1px; min-width:0; }
  .who b { font-size:15px; font-weight:650; letter-spacing:-.2px; }
  .who span { font-size:11.5px; color:var(--muted); display:flex; align-items:center; gap:5px; }
  .live { width:6px; height:6px; border-radius:50%; background:var(--muted); flex:none; }
  .live.on { background:var(--accent); box-shadow:0 0 0 3px rgba(0,217,154,.15); }
  .bar-actions { margin-left:auto; display:flex; gap:6px; }
  .icon-btn {
    background:transparent; border:1px solid var(--line); color:var(--muted);
    border-radius:9px; padding:6px 10px; font-size:11.5px; cursor:pointer;
    transition:.15s;
  }
  .icon-btn:hover { color:var(--text); border-color:#33405a; background:var(--surface); }

  /* ---- token gate ---- */
  .gate { flex:1; display:flex; flex-direction:column; justify-content:center; padding:26px 22px; gap:14px; }
  .gate h2 { margin:0; font-size:19px; font-weight:650; letter-spacing:-.3px; }
  .gate p { margin:0; color:var(--muted); font-size:13px; line-height:1.6; }
  .gate code {
    background:var(--surface); border:1px solid var(--line); border-radius:6px;
    padding:1px 6px; font-size:11.5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  .field { display:flex; flex-direction:column; gap:7px; margin-top:4px; }
  .field label { font-size:11.5px; color:var(--muted); font-weight:550; letter-spacing:.2px; }
  .field input {
    background:#0a0d13; color:var(--text);
    border:1px solid var(--line); border-radius:11px;
    padding:12px 13px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    font-size:12px; outline:none; transition:.15s; width:100%;
  }
  .field input:focus { border-color:var(--accent-dim); box-shadow:0 0 0 3px rgba(0,217,154,.1); }
  .primary {
    background:linear-gradient(135deg, var(--accent), var(--accent-dim));
    color:#04120d; border:0; border-radius:11px; padding:12px 16px;
    font-size:14px; font-weight:650; cursor:pointer; transition:.15s;
  }
  .primary:hover { filter:brightness(1.08); }
  .primary:disabled { opacity:.45; cursor:default; filter:none; }
  .gate-err { color:var(--danger); font-size:12px; min-height:15px; }

  /* ---- conversation ---- */
  #log {
    flex:1; overflow-y:auto; overscroll-behavior:contain;
    display:flex; flex-direction:column; gap:9px;
    padding:18px 16px 6px;
    scrollbar-width:thin; scrollbar-color:#2a3348 transparent;
  }
  #log::-webkit-scrollbar { width:7px; }
  #log::-webkit-scrollbar-thumb { background:#2a3348; border-radius:4px; }

  .msg {
    max-width:85%; padding:11px 14px; border-radius:var(--radius);
    white-space:pre-wrap; overflow-wrap:anywhere; font-size:14.5px; line-height:1.55;
    animation:rise .22s ease-out;
  }
  @keyframes rise { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

  .user {
    align-self:flex-end; color:#fff;
    background:linear-gradient(135deg, #4c8dff, #2563eb);
    border-bottom-right-radius:6px;
    box-shadow:0 4px 14px -6px rgba(37,99,235,.7);
  }
  .bot {
    align-self:flex-start; background:var(--surface);
    border:1px solid var(--line); border-bottom-left-radius:6px;
  }
  .err {
    align-self:flex-start; background:rgba(255,107,122,.09);
    border:1px solid rgba(255,107,122,.3); color:#ffc4cb;
    font-size:13.5px;
  }

  /* Welcome card + starter chips */
  .welcome { align-self:stretch; padding:10px 4px 2px; animation:rise .3s ease-out; }
  .welcome h3 { margin:0 0 6px; font-size:15.5px; font-weight:650; letter-spacing:-.2px; }
  .welcome p { margin:0 0 14px; color:var(--muted); font-size:13px; line-height:1.6; }
  .chips { display:flex; flex-wrap:wrap; gap:7px; }
  .chip {
    background:var(--surface); border:1px solid var(--line); color:var(--text);
    border-radius:999px; padding:8px 13px; font-size:12.5px; cursor:pointer;
    transition:.15s; text-align:left;
  }
  .chip:hover { border-color:var(--accent-dim); background:var(--surface-2); transform:translateY(-1px); }

  /* Thinking dots */
  .typing { display:flex; gap:4px; align-items:center; padding:13px 15px; }
  .typing i {
    width:6px; height:6px; border-radius:50%; background:var(--muted);
    display:block; animation:blink 1.3s infinite;
  }
  .typing i:nth-child(2){ animation-delay:.18s }
  .typing i:nth-child(3){ animation-delay:.36s }
  @keyframes blink { 0%,65%,100%{opacity:.22; transform:scale(.85)} 32%{opacity:1; transform:scale(1)} }

  /* Live lookup line - scaffolding around the answer, gone once text lands */
  .status {
    align-self:flex-start; display:flex; align-items:center; gap:8px;
    color:var(--muted); font-size:12.5px; padding:3px 6px;
    animation:rise .2s ease-out;
  }
  .pulse {
    width:7px; height:7px; border-radius:50%; background:var(--accent);
    animation:pulse 1.2s infinite; flex:none;
  }
  @keyframes pulse { 0%,100%{opacity:.25; transform:scale(.8)} 50%{opacity:1; transform:scale(1)} }

  /* Caret while the answer is being written, so a pause reads as
     "still typing" rather than "finished, badly". */
  .writing::after {
    content:""; display:inline-block; width:2px; height:15px;
    margin-left:3px; background:var(--accent); vertical-align:-3px;
    animation:pulse .9s infinite;
  }

  .meta {
    align-self:flex-start; color:#6f7994; font-size:11px;
    padding:0 6px 4px; display:flex; flex-wrap:wrap; gap:4px 8px;
  }
  .meta .checked { color:var(--accent-dim); }

  /* Product card - the one real photo for the top search hit. Everything
     else stays text, same as before; this is additive, not a redesign. */
  .product-card {
    align-self:flex-start; width:min(72%, 240px);
    background:var(--surface); border:1px solid var(--line);
    border-radius:var(--radius); overflow:hidden;
    animation:rise .22s ease-out;
  }
  .product-card .shot {
    width:100%; aspect-ratio:1/1; object-fit:cover;
    background:var(--surface-2); display:block;
  }
  .product-card .info { padding:10px 12px 12px; }
  .product-card .pname {
    font-size:13.5px; font-weight:600; line-height:1.35;
    margin:0 0 4px; overflow:hidden; text-overflow:ellipsis;
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  }
  .product-card .pprice { display:flex; align-items:baseline; gap:7px; }
  .product-card .pnow { font-size:14.5px; font-weight:700; color:var(--accent); }
  .product-card .pwas {
    font-size:12px; color:var(--muted); text-decoration:line-through;
  }
  .product-card { cursor:pointer; transition:.15s; }
  .product-card:hover { border-color:var(--accent-dim); transform:translateY(-1px); }

  /* ---- product detail sheet - opens over the conversation on a
     card tap, closes back into it. Real data, fetched fresh each
     open (see openProduct) - never the trimmed, image-less copy the
     LLM works from. ---- */
  .sheet-backdrop {
    position:absolute; inset:0; z-index:5;
    background:rgba(4,6,10,.55); backdrop-filter:blur(2px);
    display:flex; align-items:flex-end;
    animation:fadeIn .18s ease-out;
  }
  @keyframes fadeIn { from{opacity:0} to{opacity:1} }
  .sheet {
    width:100%; max-height:88%; overflow-y:auto;
    background:var(--frame); border:1px solid var(--line);
    border-radius:22px 22px 0 0; padding:0 0 20px;
    animation:slideUp .22s cubic-bezier(.2,.8,.3,1);
  }
  @keyframes slideUp { from{transform:translateY(24px); opacity:.4} to{transform:none; opacity:1} }
  .sheet-shot {
    width:100%; aspect-ratio:1/1; object-fit:cover;
    background:var(--surface-2); display:block;
  }
  .sheet-close {
    position:sticky; top:10px; margin:10px 12px 0 auto;
    width:30px; height:30px; border-radius:50%; border:0; cursor:pointer;
    background:rgba(8,10,15,.65); color:#fff; font-size:16px; display:flex;
    align-items:center; justify-content:center; float:right;
  }
  .sheet-body { padding:14px 18px 0; clear:both; }
  .sheet-body h2 { margin:0 0 6px; font-size:17px; font-weight:650; letter-spacing:-.2px; }
  .sheet-price { display:flex; align-items:baseline; gap:9px; margin-bottom:10px; }
  .sheet-now { font-size:19px; font-weight:750; color:var(--accent); }
  .sheet-was { font-size:13px; color:var(--muted); text-decoration:line-through; }
  .sheet-desc { font-size:13.5px; color:var(--text); line-height:1.55; margin:0 0 12px; }
  .sheet-row {
    display:flex; justify-content:space-between; font-size:12.5px;
    color:var(--muted); padding:7px 0; border-top:1px solid var(--line-soft);
  }
  .sheet-row span:last-child { color:var(--text); text-align:right; }
  .sheet-variants { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 4px; }
  .vpill {
    font-size:11.5px; padding:5px 10px; border-radius:999px;
    border:1px solid var(--line); color:var(--muted);
  }
  .vpill.instock { color:var(--text); border-color:#33405a; }
  .buy-btn {
    width:100%; margin-top:16px; padding:14px; border:0; border-radius:13px;
    background:linear-gradient(135deg, var(--accent), var(--accent-dim));
    color:#04120d; font-size:14.5px; font-weight:700; cursor:pointer;
  }
  .buy-note {
    font-size:11.5px; color:var(--muted); text-align:center;
    margin-top:9px; line-height:1.5;
  }

  /* ---- composer ---- */
  .composer {
    display:flex; gap:9px; padding:12px 14px 16px;
    border-top:1px solid var(--line-soft);
    background:linear-gradient(0deg, rgba(255,255,255,.02), transparent);
  }
  .composer input {
    flex:1; min-width:0; background:var(--surface); color:var(--text);
    border:1px solid var(--line); border-radius:13px;
    padding:13px 15px; font-size:14.5px; outline:none; transition:.15s;
  }
  .composer input:focus { border-color:#33405a; background:var(--surface-2); }
  .composer input:disabled { opacity:.5; }
  .send {
    width:44px; flex:none; border:0; border-radius:13px; cursor:pointer;
    background:linear-gradient(135deg, var(--accent), var(--accent-dim));
    color:#04120d; font-size:17px; font-weight:700; transition:.15s;
  }
  .send:hover:not(:disabled) { filter:brightness(1.08); }
  .send:disabled { opacity:.35; cursor:default; }

  /* ---- on a real phone, drop the frame ---- */
  @media (max-width:520px) {
    body { padding:0; align-items:stretch; }
    .device {
      max-width:none; height:100dvh; border:0; border-radius:0;
      box-shadow:none; background:var(--bg);
    }
    .bar { padding-top:max(18px, env(safe-area-inset-top)); }
    .composer { padding-bottom:max(16px, env(safe-area-inset-bottom)); }
  }
  @media (prefers-reduced-motion:reduce) {
    * { animation:none !important; transition:none !important; }
  }
</style>

<div class="device">
  <div class="bar">
    <div class="mark">Z</div>
    <div class="who">
      <b>Zatch Assistant</b>
      <span><i class="live" id="live"></i><span id="sess">not connected</span></span>
    </div>
    <div class="bar-actions">
      <button class="icon-btn" id="newBtn" onclick="newSession()" hidden>New chat</button>
    </div>
  </div>

  <div class="gate" id="gate">
    <h2>Connect to your account</h2>
    <p>Enter a Zatch username. A signed access token is generated for
       that exact user behind the scenes — the assistant then answers
       only from their real data.</p>
    <div class="field">
      <label for="username">USERNAME</label>
      <input id="username" placeholder="e.g. Pavan" autocomplete="off"
             spellcheck="false" onkeydown="if(event.key==='Enter')start()">
    </div>
    <div class="gate-err" id="gateErr"></div>
    <button class="primary" id="startBtn" onclick="start()">Start chatting</button>
  </div>

  <div id="log" hidden></div>

  <div class="composer" id="composer" hidden>
    <input id="q" placeholder="Ask about an order, a product, bargaining..."
           onkeydown="if(event.key==='Enter')send()" disabled>
    <button class="send" id="send" onclick="send()" disabled aria-label="Send">&uarr;</button>
  </div>

  <div id="sheetLayer"></div>
</div>

<script>
let token = "", session = "", busy = false, currentUser = "";

// QUESTIONS THAT WORK AGAINST ANY DATASET. Every one leans on the
// signed-in user's own records or on a general listing, so none of them
// depends on a particular product existing - which matters because the
// demo database holds about a dozen.
const STARTERS = [
  "Where is my order?",
  "What's in my cart?",
  "What's trending right now?",
  "Any live sessions on?",
  "What have I saved?",
  "Do I have any unread notifications?",
];

// WHAT THE "CHECKED" LINE CALLS EACH SOURCE.
//
// The status events carry a gerund - "Looking up your orders" - which
// is right while it is happening and wrong afterwards: "checked looking
// up your orders" is not a sentence. These are the noun forms, keyed on
// the tool name the event also carries, and grouped so that three
// catalogue queries read as one source rather than three.
const SOURCES = {
  get_order_status:"your orders", get_order_history:"your orders",
  get_order_detail:"your orders", get_delivery_estimate:"your orders",
  get_tracking:"your orders", get_invoice:"your orders",
  check_cancellation_eligibility:"your orders",
  search_products:"the catalogue", search_products_by_name:"the catalogue",
  search_products_semantically:"the catalogue", find_similar_products:"the catalogue",
  get_product_detail:"the catalogue", get_variant_stock:"the catalogue",
  get_trending_products:"the catalogue", get_recommendations:"the catalogue",
  check_bargain_eligibility:"bargaining rules", suggest_offer_amount:"bargaining rules",
  get_bargain_status:"your offers", get_counter_offer:"your offers",
  get_live_now:"live sessions", get_session_products:"live sessions",
  get_session_recap:"live sessions",
  get_trending_bits:"Bits", search_by_hashtag:"Bits", get_tagged_products:"Bits",
  get_product_reviews:"reviews", get_seller_info:"seller profiles",
  get_seller_trust_info:"seller profiles",
  get_cart:"your cart", check_coupon_validity:"coupons",
  get_saved_items:"your saved items", get_unread_notifications:"your notifications",
  get_default_address:"your address", get_followers_or_following:"your followers",
};

const $ = (id) => document.getElementById(id);

function newSession() {
  // A FRESH ID, not a cleared screen. The server keys history by session
  // id, so this is what actually starts a new conversation - and long
  // ones get expensive, since every turn resends the history.
  session = "demo-" + Math.random().toString(36).slice(2, 8);
  $("sess").textContent = (currentUser ? currentUser + " · " : "") + session;
  $("log").innerHTML = "";
  welcome();
}

function welcome() {
  const w = document.createElement("div");
  w.className = "welcome";
  const h = document.createElement("h3");
  h.textContent = "Ask me about your Zatch account";
  const p = document.createElement("p");
  p.textContent = "Orders and delivery, the catalogue, bargaining, live sessions and Bits. I read your real data — and I'll say so when I can't find something.";
  const chips = document.createElement("div");
  chips.className = "chips";
  STARTERS.forEach((s) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = s;
    b.onclick = () => { $("q").value = s; send(); };
    chips.appendChild(b);
  });
  w.append(h, p, chips);
  $("log").appendChild(w);
}

async function start() {
  const username = $("username").value.trim();
  if (!username) { $("gateErr").textContent = "Enter a username to continue."; return; }

  $("gateErr").textContent = "";
  $("startBtn").disabled = true;
  $("startBtn").textContent = "Connecting…";

  try {
    // The token never touches this page by hand - the server looks up
    // the username, mints a token for THAT user's real id, and hands it
    // back. See app/api/routes/demo_ui.py:demo_login for why this only
    // exists behind DEMO_UI_ENABLED.
    const r = await fetch("/demo/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      $("gateErr").textContent = data.detail || ("Couldn't log in (HTTP " + r.status + ").");
      return;
    }
    token = data.token;
    currentUser = data.username;
  } catch (e) {
    $("gateErr").textContent = "Couldn't reach the server. Is it still running?";
    return;
  } finally {
    $("startBtn").disabled = false;
    $("startBtn").textContent = "Start chatting";
  }

  $("gate").hidden = true;
  $("log").hidden = false;
  $("composer").hidden = false;
  $("newBtn").hidden = false;
  $("live").classList.add("on");
  if (!session) newSession();
  setBusy(false);
  $("q").focus();
}

function setBusy(state) {
  busy = state;
  $("q").disabled = state;
  $("send").disabled = state;
  if (!state) $("q").focus();
}

// Only follow the conversation down if the reader is already near the
// bottom - otherwise scrolling back to re-read an answer fights every
// token that arrives.
function atBottom() {
  const el = $("log");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 90;
}
function stick(force) {
  const el = $("log");
  if (force || atBottom()) el.scrollTop = el.scrollHeight;
}

function add(text, cls) {
  const d = document.createElement("div");
  d.className = cls;
  // textContent, never innerHTML: model output is not trusted markup,
  // and product descriptions are written by sellers.
  d.textContent = text;
  $("log").appendChild(d);
  stick(true);
  return d;
}

function dropWelcome() {
  const w = $("log").querySelector(".welcome");
  if (w) w.remove();
}

// One real photo for the top search hit, shown alongside (not instead
// of) the text answer, which still names the other matches in words.
// Built with createElement/textContent throughout - a product name or
// image URL is seller-supplied content, never trusted as markup.
function addProductCard(product) {
  if (!product || !product.image) return;

  const card = document.createElement("div");
  card.className = "product-card";

  const img = document.createElement("img");
  img.className = "shot";
  img.src = product.image;
  img.alt = product.name || "Product photo";
  img.loading = "lazy";
  // A dead CDN link should disappear, not leave a broken-image icon in
  // the middle of the conversation.
  img.onerror = () => card.remove();

  const info = document.createElement("div");
  info.className = "info";

  const name = document.createElement("p");
  name.className = "pname";
  name.textContent = product.name || "Product";

  const priceRow = document.createElement("div");
  priceRow.className = "pprice";
  const hasDiscount =
    product.discountedPrice != null && product.discountedPrice !== product.price;

  const now = document.createElement("span");
  now.className = "pnow";
  now.textContent = "₹" + (hasDiscount ? product.discountedPrice : product.price);
  priceRow.appendChild(now);

  if (hasDiscount && product.price != null) {
    const was = document.createElement("span");
    was.className = "pwas";
    was.textContent = "₹" + product.price;
    priceRow.appendChild(was);
  }

  info.append(name, priceRow);
  card.append(img, info);
  card.onclick = () => openProduct(product.product_id);
  $("log").appendChild(card);
  stick(true);
}

// TAPPING THE CARD OPENS THE REAL PRODUCT, per the user's own words -
// "as if I click on that, that directly going to product". Fetches
// FRESH from /demo/product/<id> rather than reusing the card's data,
// because the card only ever carries name/price/one photo (see
// _product_card in tool_executor.py) - the sheet needs the full
// description, every image, and per-variant stock the LLM is never
// even sent.
async function openProduct(productId) {
  if (!productId) return;
  closeSheet();

  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";
  backdrop.onclick = (e) => { if (e.target === backdrop) closeSheet(); };

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.innerHTML = ""; // populated below, node by node - no raw HTML from data

  const closeBtn = document.createElement("button");
  closeBtn.className = "sheet-close";
  closeBtn.textContent = "✕";
  closeBtn.onclick = closeSheet;

  const body = document.createElement("div");
  body.className = "sheet-body";
  const loading = document.createElement("p");
  loading.className = "sheet-desc";
  loading.textContent = "Loading product...";
  body.appendChild(loading);

  sheet.append(closeBtn, body);
  backdrop.appendChild(sheet);
  $("sheetLayer").appendChild(backdrop);

  try {
    const r = await fetch("/demo/product/" + encodeURIComponent(productId));
    if (!r.ok) throw new Error("not found");
    const p = await r.json();
    renderProductSheet(sheet, body, p);
  } catch (e) {
    body.innerHTML = "";
    const err = document.createElement("p");
    err.className = "sheet-desc";
    err.textContent = "Couldn't load this product right now.";
    body.appendChild(err);
  }
}

function closeSheet() {
  $("sheetLayer").innerHTML = "";
}

function renderProductSheet(sheet, body, p) {
  const images = (p.images || []).map((i) => i && i.url).filter(Boolean);
  if (images.length) {
    const shot = document.createElement("img");
    shot.className = "sheet-shot";
    shot.src = images[0];
    shot.alt = p.name || "Product photo";
    sheet.insertBefore(shot, body);
  }

  body.innerHTML = "";

  const h2 = document.createElement("h2");
  h2.textContent = p.name || "Product";
  body.appendChild(h2);

  const priceRow = document.createElement("div");
  priceRow.className = "sheet-price";
  const hasDiscount = p.discountedPrice != null && p.discountedPrice !== p.price;
  const now = document.createElement("span");
  now.className = "sheet-now";
  now.textContent = "₹" + (hasDiscount ? p.discountedPrice : p.price);
  priceRow.appendChild(now);
  if (hasDiscount && p.price != null) {
    const was = document.createElement("span");
    was.className = "sheet-was";
    was.textContent = "₹" + p.price;
    priceRow.appendChild(was);
  }
  body.appendChild(priceRow);

  if (p.description) {
    const desc = document.createElement("p");
    desc.className = "sheet-desc";
    desc.textContent = p.description;
    body.appendChild(desc);
  }

  if (p.condition) {
    const row = document.createElement("div");
    row.className = "sheet-row";
    row.append(span("Condition"), span(p.condition));
    body.appendChild(row);
  }

  const variants = p.variants || [];
  if (variants.length) {
    const label = document.createElement("div");
    label.className = "sheet-row";
    label.append(span("Available as"), span(""));
    body.appendChild(label);

    const pills = document.createElement("div");
    pills.className = "sheet-variants";
    for (const v of variants) {
      const pill = document.createElement("span");
      const inStock = !v.isOutOfStock && (v.stock || 0) > 0;
      pill.className = "vpill" + (inStock ? " instock" : "");
      const bits = [v.color, v.size].filter(Boolean);
      pill.textContent = (bits.join(" / ") || "Option") + (inStock ? "" : " (out of stock)");
      pills.appendChild(pill);
    }
    body.appendChild(pills);
  }

  if (p.shipping) {
    const row = document.createElement("div");
    row.className = "sheet-row";
    const free = p.shipping.isFree || p.shipping.freeShipping;
    row.append(span("Shipping"), span(free ? "Free" : "Charged at checkout"));
    body.appendChild(row);
  }

  const buyBtn = document.createElement("button");
  buyBtn.className = "buy-btn";
  buyBtn.textContent = "Buy Now";
  const note = document.createElement("p");
  note.className = "buy-note";
  note.textContent =
    "This assistant only reads Zatch data - it never places orders. " +
    "In the finished app, this opens checkout in Zatch for this product.";
  buyBtn.onclick = () => { note.textContent = "Checkout for \"" + (p.name || "this product") + "\" would open in the Zatch app here - not simulated in this demo."; };
  body.append(buyBtn, note);
}

function span(text) {
  const s = document.createElement("span");
  s.textContent = text;
  return s;
}

async function send() {
  if (busy) return;
  const box = $("q");
  const text = box.value.trim();
  if (!text) return;

  dropWelcome();
  box.value = "";
  setBusy(true);
  add(text, "msg user");

  // Three elements, in the order the user meets them: dots while the
  // model decides anything, a status line naming each lookup, then the
  // answer typing itself out. The first two go as soon as the third has
  // something in it.
  const wait = add("", "msg bot typing");
  wait.innerHTML = "<i></i><i></i><i></i>";
  let status = null, bubble = null, firstToken = null;
  const checked = [];
  const t0 = performance.now();

  function showStatus(label) {
    if (!status) {
      status = document.createElement("div");
      status.className = "status";
      status.innerHTML = '<span class="pulse"></span><span class="label"></span>';
      $("log").appendChild(status);
    }
    status.querySelector(".label").textContent = label;
    stick();
  }

  function clearScaffolding() {
    if (wait) wait.remove();
    if (status) { status.remove(); status = null; }
  }

  function appendToken(t) {
    if (!bubble) {
      clearScaffolding();
      bubble = add("", "msg bot writing");
      firstToken = performance.now() - t0;
    }
    bubble.textContent += t;
    stick();
  }

  function finish(reply) {
    clearScaffolding();
    // The reply from `done` is authoritative. Usually identical to what
    // the tokens drew - but the fallback answers (rate limited, provider
    // down) never arrive as tokens at all, and this is what puts them on
    // screen without the client needing to know they are special.
    if (!bubble) bubble = add("", "msg bot");
    if (reply) bubble.textContent = reply;
    bubble.classList.remove("writing");

    const ms = performance.now() - t0;
    const m = document.createElement("div");
    m.className = "meta";
    const timing = document.createElement("span");
    // Both numbers, because they are the point: the wait the user FELT
    // is the first one, not the second.
    timing.textContent = firstToken
      ? (firstToken / 1000).toFixed(1) + "s to first word · " + (ms / 1000).toFixed(1) + "s total"
      : (ms / 1000).toFixed(1) + "s";
    m.appendChild(timing);
    if (checked.length) {
      const c = document.createElement("span");
      c.className = "checked";
      // The provenance of the answer, left on screen: this is what makes
      // "it reads real data" visible instead of merely asserted.
      c.textContent = "✓ checked " + checked.join(" · ");
      m.appendChild(c);
    }
    $("log").appendChild(m);
    stick();
  }

  function fail(message) {
    clearScaffolding();
    add(message, "msg err");
  }

  try {
    // fetch, not EventSource: EventSource cannot POST and cannot send an
    // Authorization header, and the endpoint requires both.
    const r = await fetch("/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
      body: JSON.stringify({message: text, session_id: session}),
    });

    if (r.status === 401) { fail("That token has expired or is invalid — generate a new one and reload."); return; }
    if (r.status === 429) { fail("Too many questions at once. Give it a few seconds and try again."); return; }
    if (!r.ok)            { fail("The server returned HTTP " + r.status + "."); return; }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      // SSE frames are separated by a blank line. A partial frame stays
      // in the buffer until the rest of it arrives - chunk boundaries do
      // not respect message boundaries.
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let name = "", data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        const payload = JSON.parse(data);

        if (name === "status") {
          const source = SOURCES[payload.tool];
          if (source && !checked.includes(source)) checked.push(source);
          showStatus(payload.label);
        }
        else if (name === "product") addProductCard(payload.product);
        else if (name === "token") appendToken(payload.text);
        else if (name === "done")  finish(payload.reply);
        else if (name === "error") { fail(payload.message); return; }
      }
    }
  } catch (e) {
    fail("Couldn't reach the server. Is it still running?");
  } finally {
    setBusy(false);
  }
}

// A quiet liveness check, so the header dot is telling the truth rather
// than being decorative. Failure is silent: the dot simply stays grey.
fetch("/health").then(r => r.json()).then(h => {
  if (h.status === "ok") $("live").classList.add("on");
}).catch(() => {});
</script>
"""


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo_ui() -> str:
    return PAGE

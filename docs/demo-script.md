# Zatch Assistant - Demo Run Sheet

Every answer below was produced by an actual run against the demo
database. Ask the questions in this order; the sequence builds.

**Use the same `session_id` throughout** - several questions depend
on the ones before them.

## Before you start

Run these in order. Each prevents a specific way the demo breaks.

```
uv run python scripts/inspect_data.py     # must say: OK demo data
uv run python scripts/check_redis.py      # must say: OK connected
uv run python scripts/generate_test_token.py
uv run python scripts/warm_cache.py       # must say: CACHE WARM
uv run uvicorn app.api.main:app
```

| Step | Prevents |
|---|---|
| `inspect_data` | demoing against REAL customer data by mistake |
| `check_redis` | memory vanishing mid-demo, so follow-ups lose context |
| `generate_test_token` | a 401 two minutes in - tokens last 2 hours |
| `warm_cache` | the first question being slow and full price |
| no `--reload` | an accidental file save wiping every conversation |

Warm the cache a minute or two before you begin, not the night
before - it expires after 2 hours idle.

---

## 1. It knows the user's own data

**Ask:** `where is my order`

**It answered:**

> I found several orders that are still in progress:
> 
> digital clock — 1378 — confirmed  
> 8x10 size frame — 2662 — confirmed  
> black — 7278 — confirmed  
> black — 1467 — confirmed  
> Shirt — 3238 — confirmed  
> Shirt — 7520 — out for delivery
> 
> Which one do you mean? Reply with the last 4 digits of the order.

**Why this lands:** Real orders, straight from the database. It lists them and asks which - it does not guess.

*Cost: 4092 prompt tokens*

---

## 2. ...and remembers the conversation

**Ask:** `tell me about the digital clock one`

**It answered:**

> Details for your order ending 1378:
> 
> Status: confirmed — expected delivery 27 Jul 2026 (overdue by 30 days)  
> Delivery city: Surat, Gujarat
> 
> Items:
> - digital clock — Qty 1 — ₹10 — no color/size variant  
> - kurtha — Qty 1 — ₹10 — color: green, size: M
> 
> Pricing: subtotal ₹20, shipping ₹10, tax ₹3.05, total ₹30
> 
> To see tracking, invoice or full order timeline, open the Orders screen in the app and tap this order.

**Why this lands:** Refers back to an item from the previous answer. It resolves which order that is from context alone.

*Cost: 5351 prompt tokens*

---

## 3. It knows the catalogue

**Ask:** `what do you have in Men?`

**It answered:**

> I found 7 items in Men:
> 
> Italian pants — ₹800 (now ₹400)  
> Turtleneck shirts — ₹999 (now ₹899)  
> Leather jacket — ₹1,499 (now ₹650)  
> Strips Collection — ₹499 (now ₹349)  
> Lenin shirts — ₹599  
> Shirt — ₹20 (now ₹19)  
> Jeans — ₹1,000 (now ₹800)
> 
> Which one would you like details for? You can also tap any item in the Men section to view it.

**Why this lands:** Filters on the real category value 'Men' - the values are read from the database and embedded in the prompt, so it cannot invent one.

*Cost: 6416 prompt tokens*

---

## 4. ...including what things are LIKE

**Ask:** `anything similar to the Italian pants?`

**It answered:**

> Here are items similar to the Italian pants:
> 
> jeans — ₹1,000 (now ₹800)  
> Baggy Blue Street — ₹2,099 (now ₹1,449)  
> Mens Formal Trousers — ₹500 (now ₹450)  
> Denim Model — ₹1,449 (now ₹1,349)
> 
> Which one would you like details for, or should I open the Men section in the app so you can browse?

**Why this lands:** Vector search over Zatch's own embeddings - already in the database, previously unused by the assistant.

*Cost: 7284 prompt tokens*

---

## 5. Bargaining, the signature feature

**Ask:** `can I bargain on the Italian pants?`

**It answered:**

> Yes — you can bargain on the Italian pants.
> 
> Seller settings:
> - auto‑accepts offers up to 5% off the listed price (these are accepted immediately)  
> - maximum discount allowed is 30% (seller won’t accept more than this)
> 
> Current price: ₹400 (was ₹800).
> 
> Would you like me to suggest a reasonable offer amount?

**Why this lands:** Resolves the product by name, then reads that seller's own bargain settings.

*Cost: 7843 prompt tokens*

---

## 6. ...and money questions

**Ask:** `is SUMMER still valid?`

**It answered:**

> SUMMER is not valid — it’s expired.
> 
> If you want, tell me another coupon code to check, or I can check which coupons are valid for your current cart.

**Why this lands:** Read-only coupon check. It reports validity; it never applies anything.

*Cost: 8165 prompt tokens*

---

## 7. It refuses to act, by design

**Ask:** `can I cancel order ORD1784536807731378?`

**It answered:**

> Yes — your order ending 1378 (status: confirmed) can be cancelled.
> 
> I can’t cancel it here — open the Orders screen in the app, tap that order, and choose Cancel to proceed.

**Why this lands:** Reports eligibility, then points at the Orders screen. The assistant has no write access to anything.

*Cost: 8398 prompt tokens*

---

## 8. THE CLOSER: it cannot leak another customer's data

**Ask:** `what is the status of order ORD1772882441677323`

**It answered:**

> I couldn’t find any order with ID ORD1772882441677323 on your account. I checked the order status and your recent orders but there’s no match.
> 
> Please double-check the ID and try again (you can reply with the last 4 digits), or open the Orders screen in the app to view all your orders.

**Why this lands:** Order ORD1772882441677323 is REAL and belongs to a different buyer. The query is scoped by the verified token, so the database never returns it - the assistant is not choosing to withhold it, it structurally cannot see it.

*Cost: 13598 prompt tokens*

---

## Token budget

- Total for the full demo: **61,147 prompt tokens**
- Average per question: **7,643**
- Groq free tier: **8,000 per minute**, shared across all users

At the free tier this demo needs about **7.6 minutes** of
elapsed time no matter how fast anyone types. On a paid key it runs
at conversation speed.

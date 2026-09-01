"""
WHAT:
    Executes a tool call the LLM requested, injecting the verified
    user_id server-side, and TRIMS large results down to only what the
    LLM actually needs before sending them back into the conversation.

WHY THIS APPROACH:
    Real testing revealed a distinct problem from Phase 3's data
    safety: search_products returning 20 full product documents (each
    with complete image arrays, full variant/image trees, shipping
    details) produced over 51,000 tokens for ONE tool result -
    blowing well past the free tier's token-per-minute limit.
    Phase 3's allowlist governs what's safe to leave the DATABASE; this
    trimming step is a SEPARATE concern - what's actually USEFUL to
    hand the LLM, matching what Phase 5's response templates reference
    (name, price, category - never raw image URLs).

FLOW:
    orchestrator.py calls execute_tool() for every tool_call the LLM
    requests, then sends the JSON-safe, TRIMMED result back to the LLM.

LOGIC:
    Only list-returning / detail-returning tools need trimming - order
    and bargain results are already small and specific per Phase 5's
    own response shapes. LLM_LIST_LIMIT_CAP hard-caps how many results
    are sent regardless of what the model requests, so a single tool
    call can never again blow the token budget this way.

    TRIMMERS DO TWO JOBS, NOT ONE. Shrinking is the obvious one. The
    second is making a result USEFUL: a live session's productSequence
    and a Bit's products are raw ObjectIds, and no amount of them lets
    the model say "the blue jacket is up next". Those two trimmers are
    async and enrich the IDs into real names and prices via
    products_repo.get_products_by_ids() - one batched query, not one
    per product.

    Live-session and Bit comments carry the commenting user's userId.
    Public or not, it is an internal ID the model is instructed never
    to surface, so the comment trimmer keeps only username and text.

MECHANISM:
    TOOL_REGISTRY (from tools.py) provides the real function and
    whether it needs user_id. to_json_safe() converts MongoDB types.
    _TRIMMERS maps specific tool names to a function that strips a
    result down before it's ever serialized; a trimmer may be sync or
    async, and execute_tool awaits it when needed.
"""

import inspect
import json
from datetime import datetime, timezone

import structlog
from bson import ObjectId

from app.agent.tools import TOOL_REGISTRY
from app.repos import products_repo

logger = structlog.get_logger()

# Hard cap - regardless of what limit the LLM requests, list results
# are capped here. Prevents a repeat of the 51k-token overflow.
LLM_LIST_LIMIT_CAP = 8

# Comments are free text of unbounded length and count. A handful is
# enough for the model to characterise the mood of a session.
MAX_COMMENTS = 3

# Tools whose result is a list of products the shopper is actually
# BROWSING (as opposed to get_product_detail, which is one product
# already named). For these, the UI shows the TOP hit as a real image
# card - text alone reads as a search engine, not a shop. The other
# matches stay text-only (name + price), exactly as the model already
# writes them - this only adds a picture for the one result worth
# looking at.
PRODUCT_CARD_TOOLS = {
    "search_products",
    "search_products_by_name",
    "search_products_semantically",
    "find_similar_products",
    "get_trending_products",
    "get_recommendations",
}


def _product_image_url(product: dict) -> str | None:
    """First real photo, if the product has one. Sold from Zatch's own
    CDN (bunny.net) - the same URL the mobile app itself renders, so
    nothing new is exposed here that the app doesn't already show."""
    for image in product.get("images") or []:
        if isinstance(image, dict) and image.get("url"):
            return image["url"]
    return None


def _product_card(product: dict) -> dict:
    """The UI-only counterpart to _trim_product_list: same identifying
    fields, PLUS the image the LLM is never sent (see _trim_product_list's
    docstring on token cost). Built from the RAW repo result, before
    trimming drops the images array."""
    return {
        "product_id": str(product.get("_id")),
        "name": product.get("name"),
        "price": product.get("price"),
        "discountedPrice": product.get("discountedPrice"),
        "image": _product_image_url(product),
    }


def to_json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json_safe(v) for v in value]
    return value


def _trim_product_list(products: list) -> list:
    """Used for search_products / get_trending_products /
    get_recommendations - strips each product down to only what Phase
    5's response templates actually reference (name + price), dropping
    images, full variant trees, shipping, tags, analytics entirely."""
    # DEDUPED ON (name, price). The real catalogue contains several
    # products with the same name at the same price - three "jeans" at
    # Rs.800 appeared in a measured similar-products result. They are
    # distinct rows in the database, but to a shopper they are the same
    # line repeated three times, and a list that repeats itself reads as
    # broken rather than thorough.
    #
    # A DIFFERENT price is kept: same name, different price is a real
    # choice the user can act on. Only the genuinely indistinguishable
    # collapse.
    seen = set()
    out = []
    for p in products or []:
        key = ((p.get("name") or "").strip().lower(), p.get("discountedPrice"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "product_id": str(p.get("_id")),
            "name": p.get("name"),
            "price": p.get("price"),
            "discountedPrice": p.get("discountedPrice"),
            "category": p.get("category"),
        })
        if len(out) >= LLM_LIST_LIMIT_CAP:
            break
    return out


def _trim_product_detail(detail: dict | None) -> dict | None:
    """Used for get_product_detail - keeps the fields PDF §4.4's spec
    needs, drops raw image URLs and per-variant image arrays (the LLM
    never needs to read a URL to describe a product in text)."""
    if detail is None:
        return None
    variants = detail.get("variants", []) or []
    return {
        "productId": detail.get("productId"),
        "name": detail.get("name"),
        "description": detail.get("description"),
        "condition": detail.get("condition"),
        "price": detail.get("price"),
        "discountedPrice": detail.get("discountedPrice"),
        "shipping": detail.get("shipping"),
        "variantSummary": [
            {"color": v.get("color"), "size": v.get("size"), "stock": v.get("stock")}
            for v in variants
        ],
    }


DELIVERED_OR_DONE = {"delivered", "cancelled", "returned", "refunded"}


def _trim_order_history(orders: list) -> list:
    """get_order_history - the raw documents are large and, on one
    measured call, 1,574 tokens for five orders: buyerId, sellerId,
    statusHistory, review, deliveryType and the full address all travel
    for an answer that needs an item, a status and a date.

    IT ALSO COMPUTES WHETHER A DELIVERY IS LATE. A real order came back
    with expectedDelivery of 27 July while the date was 26 August, and
    the assistant reported it flatly as "expected by 27 July 2026" -
    true, and useless. A model cannot notice that without knowing
    today's date, which it does not, so the comparison is done here and
    handed over as a fact. Only for orders still in progress: a
    delivered or cancelled order is not "late".
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []

    for order in (orders or [])[:LLM_LIST_LIMIT_CAP]:
        dates = order.get("dates") or {}
        expected = dates.get("expectedDelivery")
        status = (order.get("status") or "").lower()

        row = {
            "orderId": order.get("orderId"),
            "status": order.get("status"),
            "items": [
                {"name": i.get("name"), "qty": i.get("qty")}
                for i in (order.get("items") or [])[:4]
            ],
            "total": (order.get("pricing") or {}).get("total"),
            "expectedDelivery": expected,
        }

        if isinstance(expected, datetime) and status not in DELIVERED_OR_DONE:
            days_late = (now - expected).days
            if days_late > 0:
                row["isOverdue"] = True
                row["daysLate"] = days_late
        out.append(row)

    return out


def _trim_comments(comments: list | None) -> list:
    """Keeps username + text only. userId is deliberately dropped - it
    is an internal ID, and the system prompt forbids surfacing those."""
    recent = (comments or [])[-MAX_COMMENTS:]
    out = []
    for comment in recent:
        if isinstance(comment, dict):
            text = comment.get("text") or comment.get("comment")
            if text:
                out.append({"username": comment.get("username"), "text": text})
        elif isinstance(comment, str):
            out.append({"username": None, "text": comment})
    return out


def _trim_live_sessions(sessions: list) -> list:
    """get_live_now - drops thumbnails, product arrays and full comment
    threads; a "what's live" answer needs a title and an audience size."""
    return [
        {
            "session_id": str(s.get("_id")),
            "title": s.get("title"),
            "status": s.get("status"),
            "viewersCount": s.get("viewersCount"),
            "isTrending": s.get("isTrending"),
        }
        for s in (sessions or [])[:LLM_LIST_LIMIT_CAP]
    ]


def _trim_bits(bits: list) -> list:
    """get_trending_bits / search_by_hashtag - drops the video and
    thumbnail URLs entirely; the model describes a Bit in words and
    never needs to read a CDN link."""
    return [
        {
            "bit_id": str(b.get("_id")),
            "title": b.get("title"),
            "hashtags": (b.get("hashtags") or [])[:5],
            "likeCount": b.get("likeCount"),
            "viewCount": b.get("viewCount"),
        }
        for b in (bits or [])[:LLM_LIST_LIMIT_CAP]
    ]


def _trim_session_recap(recap: dict | None) -> dict | None:
    """get_session_recap - the comments array is unbounded, so it is
    replaced by a count plus the last few, and the raw product list by
    the count the repo already computed."""
    if recap is None:
        return None
    comments = recap.get("comments") or []
    return {
        "sessionId": recap.get("sessionId"),
        "title": recap.get("title"),
        "status": recap.get("status"),
        "peakViewers": recap.get("peakViewers"),
        "revenue": recap.get("revenue"),
        "productCount": recap.get("productCount"),
        "commentCount": len(comments),
        "recentComments": _trim_comments(comments),
    }


def _unresolved(requested: list, resolved: list) -> int:
    """How many referenced products no longer exist.

    NOT a rare edge case: 19 of 79 live sessions in the sandbox
    reference products that have since been deleted, so a session can
    legitimately list a product the catalogue cannot resolve. Reporting
    the gap explicitly stops the model promising "1 product" it is then
    unable to name - it can say the details are no longer available
    instead, which is the truth.
    """
    return max(len(requested) - len(resolved), 0)


async def _enrich_session_products(result: dict | None) -> dict | None:
    """get_session_products - productSequence is a raw ObjectId array.
    Resolved to real names and prices in ONE batched query so the model
    can actually name what is being featured."""
    if result is None:
        return None
    requested = list(result.get("productSequence") or [])[:LLM_LIST_LIMIT_CAP]
    products = await products_repo.get_products_by_ids(requested)
    return {
        "sessionId": result.get("sessionId"),
        "title": result.get("title"),
        "status": result.get("status"),
        "productCount": len(result.get("productSequence") or []),
        "products": _trim_product_list(products),
        "unavailableCount": _unresolved(requested, products),
    }


async def _enrich_tagged_products(result: dict | None) -> dict | None:
    """get_tagged_products - same reasoning as _enrich_session_products,
    for the products tagged inside a Bit."""
    if result is None:
        return None
    requested = list(result.get("products") or [])[:LLM_LIST_LIMIT_CAP]
    products = await products_repo.get_products_by_ids(requested)
    return {
        "bitId": result.get("bitId"),
        "title": result.get("title"),
        "productCount": len(result.get("products") or []),
        "products": _trim_product_list(products),
        "unavailableCount": _unresolved(requested, products),
    }


async def _enrich_cart(cart: dict | None) -> dict | None:
    """get_cart - items reference products by raw ObjectId, which the
    model cannot turn into "a Photo Frame and a Kurta". Resolved to real
    names in ONE batched query, same as the live-session and Bit
    trimmers.

    THE TOTAL IS COMPUTED HERE, and this is now the only place that
    computes it. carts_repo used to carry a get_cart_total() for PDF
    §6.2 which summed cartPrice WITHOUT multiplying by qty; measured
    against the real carts collection that is wrong (a line reading
    qty=5, cartPrice=381 sits against a unit price of 400, so cartPrice
    is per-unit). It was never called from here anyway - the items
    already carry cartPrice and qty, and a second tool round would cost
    a full prompt to learn something we can add up - so it was deleted
    rather than fixed, leaving one implementation instead of two that
    disagree.
    """
    if cart is None:
        return None
    items = cart.get("items") or []
    product_ids = [i.get("product") for i in items if i.get("product")]
    products = {p["_id"]: p for p in await products_repo.get_products_by_ids(product_ids)}

    lines, subtotal = [], 0
    for item in items:
        price = item.get("cartPrice") or 0
        qty = item.get("qty") or 0
        subtotal += price * qty
        product = products.get(item.get("product")) or {}
        lines.append({
            "name": product.get("name") or "a product no longer available",
            "variant": item.get("variant"),
            "qty": qty,
            "price": price,
        })

    return {
        "items": lines,
        "itemCount": len(lines),
        "subtotal": subtotal,
        "discount": cart.get("discount", 0),
        "total": subtotal - (cart.get("discount") or 0),
    }


async def _enrich_saved_items(saved: dict | None) -> dict | None:
    """get_saved_items - returns bare id lists, which tell the model
    nothing it can say out loud. Products are resolved to names; Bits are
    only counted, since naming them would need a second collection and
    "3 saved videos" is enough to answer the question."""
    if saved is None:
        return None
    product_ids = list(saved.get("savedProductIds") or [])[:LLM_LIST_LIMIT_CAP]
    products = await products_repo.get_products_by_ids(product_ids)
    return {
        "savedProducts": _trim_product_list(products),
        "savedProductCount": len(saved.get("savedProductIds") or []),
        "savedBitCount": len(saved.get("savedBitIds") or []),
    }


def _trim_follow_list(result: dict | None) -> dict | None:
    """get_followers_or_following - the repo resolves EVERY id in the
    list into a user document, so a popular account returns an unbounded
    array for a question that is usually about the number.

    The counts are kept whole and only the named sample is capped:
    "1,240 followers, including alice and bob" is the answer; listing
    1,240 usernames is not. rawCount and resolvedCount both survive
    because the gap between them is real - a follower whose account has
    since been deleted cannot be named, and the model should say so
    rather than promise a name it does not have.
    """
    if result is None:
        return None
    users = result.get("users") or []
    return {
        "kind": result.get("kind"),
        "rawCount": result.get("rawCount"),
        "resolvedCount": result.get("resolvedCount"),
        "sample": [
            {"username": u.get("username"), "businessName": u.get("businessName")}
            for u in users[:LLM_LIST_LIMIT_CAP]
        ],
    }


# Maps a tool name to a function that trims its raw repo result before
# it's sent to the LLM. Tools not listed here pass through unchanged -
# order/bargain/review results are already small and specific.
_TRIMMERS = {
    "get_order_history": _trim_order_history,
    "search_products": _trim_product_list,
    "get_trending_products": _trim_product_list,
    "get_recommendations": _trim_product_list,
    "get_product_detail": _trim_product_detail,
    "search_products_by_name": _trim_product_list,
    "find_similar_products": _trim_product_list,
    "search_products_semantically": _trim_product_list,
    # Live shopping
    "get_live_now": _trim_live_sessions,
    "get_session_recap": _trim_session_recap,
    "get_session_products": _enrich_session_products,  # async
    # Bits
    "get_trending_bits": _trim_bits,
    "search_by_hashtag": _trim_bits,
    "get_tagged_products": _enrich_tagged_products,  # async
    # Cart / account
    "get_cart": _enrich_cart,                        # async
    "get_saved_items": _enrich_saved_items,           # async
    "get_followers_or_following": _trim_follow_list,
}


async def execute_tool(
    tool_name: str, arguments: dict, user_id: str, on_event=None
) -> str:
    if tool_name not in TOOL_REGISTRY:
        logger.warning("unknown_tool_requested", tool_name=tool_name)
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    func, needs_user_id = TOOL_REGISTRY[tool_name]
    kwargs = dict(arguments) if arguments else {}
    if needs_user_id:
        kwargs["user_id"] = user_id  # ALWAYS server-injected, never from the LLM

    # Hard-cap any "limit" argument regardless of what the LLM requested.
    if "limit" in kwargs and isinstance(kwargs["limit"], int):
        kwargs["limit"] = min(kwargs["limit"], LLM_LIST_LIMIT_CAP)

    logger.info("tool_executing", tool_name=tool_name, arguments=arguments)
    try:
        result = await func(**kwargs)
    except Exception as exc:
        logger.error("tool_execution_failed", tool_name=tool_name, error=str(exc))
        return json.dumps({"error": "This lookup failed - please try again."})

    # IMAGE, SEPARATELY FROM THE TEXT ANSWER, AND BEFORE TRIMMING TOUCHES
    # THE RESULT. The LLM is never sent an image URL (see _trim_product_
    # list) - it cannot read a photo, and every one added real prompt
    # tokens for nothing. The UI can show one, so the top hit is handed
    # to it directly, on the wire, alongside (not instead of) the text
    # answer the model writes from the trimmed data below.
    if (
        on_event is not None
        and tool_name in PRODUCT_CARD_TOOLS
        and isinstance(result, list)
        and result
    ):
        # THE TOP hit, not just result[0]. Measured: a real "shirt" search
        # returned photographed products throughout, but that is not
        # guaranteed - a listing can exist before its photos are
        # uploaded. Scanning past a photo-less leader for the first
        # result that HAS one means a real search with real photos
        # further down still shows a card, rather than silently showing
        # none because item #1 happened to have no picture yet.
        card = next(
            (c for c in (_product_card(p) for p in result) if c["image"]),
            None,
        )
        if card is not None:
            on_event({"type": "product", "tool": tool_name, "product": card})

    trimmer = _TRIMMERS.get(tool_name)
    if trimmer is not None:
        try:
            # A trimmer may be async when it needs a second query to make
            # the result useful (ID enrichment) rather than only smaller.
            result = (
                await trimmer(result)
                if inspect.iscoroutinefunction(trimmer)
                else trimmer(result)
            )
        except Exception as exc:
            logger.error("tool_result_trim_failed", tool_name=tool_name, error=str(exc))
            return json.dumps({"error": "This lookup failed - please try again."})

    return json.dumps(to_json_safe(result), default=str)

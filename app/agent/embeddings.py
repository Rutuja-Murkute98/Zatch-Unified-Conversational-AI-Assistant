"""
WHAT:
    Turns text into a vector using Azure OpenAI, so a free-text query can
    be compared against the product embeddings stored in MongoDB.

WHY THIS EXISTS AT ALL:
    find_similar_products already does vector search without any of this,
    by reusing a product's STORED embedding as the query vector. That
    works precisely because stored-vs-stored is guaranteed to be the same
    model, whatever that model is.

    Free-text search cannot borrow that trick: "something cosy for
    winter" has no stored vector, so the query has to be embedded - which
    means knowing, and matching, the model that embedded the catalogue.

THE RULE THAT GOVERNS EVERYTHING HERE:
    A query embedded by a DIFFERENT model than the documents is not
    "slightly worse". It is meaningless. Cosine similarity between two
    unrelated vector spaces returns numbers that look entirely normal -
    ~0.0 to ~0.3, ranked, plausible - and are noise. Nothing errors,
    nothing logs, and the results read as merely mediocre rather than
    wrong.

    So this module refuses to run against a catalogue it did not embed:
    EMBEDDING_MODEL_TAG is written into every document by the re-embed
    script and checked before any search. A mismatch raises rather than
    quietly returning nonsense.

MECHANISM:
    Azure puts the deployment in the URL path rather than the request
    body, and pins behaviour to a dated api-version - the same shape as
    the chat endpoint in llm_client.py.
"""

import httpx
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()

# Stamped into every embedding document, and checked before searching.
# Change this whenever the model or dimension count changes, so a
# half-migrated catalogue is detected rather than silently searched.
EMBEDDING_MODEL_TAG = "azure:text-embedding-3-small:1536"

REQUEST_TIMEOUT_SECONDS = 30.0

# Azure caps how many inputs one embeddings request may carry. Well
# under any documented limit, and small enough that a failure costs
# little to retry.
BATCH_SIZE = 64


class EmbeddingUnavailable(Exception):
    """Raised when the embedding endpoint cannot be reached or refuses."""


class EmbeddingModelMismatch(Exception):
    """Raised when the stored catalogue was embedded by a different model.

    Deliberately fatal. The alternative is returning ranked nonsense that
    looks like a working search.
    """


def embeddings_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.azure_openai_endpoint
        and settings.azure_openai_api_key
        and settings.azure_embedding_deployment
    )


def _endpoint() -> tuple[str, dict]:
    settings = get_settings()
    base = settings.azure_openai_endpoint.rstrip("/")
    url = (
        f"{base}/openai/deployments/{settings.azure_embedding_deployment}"
        f"/embeddings?api-version={settings.azure_openai_api_version}"
    )
    return url, {"api-key": settings.azure_openai_api_key}


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeds a list of strings, in batches, preserving order."""
    if not embeddings_configured():
        raise EmbeddingUnavailable(
            "AZURE_EMBEDDING_DEPLOYMENT is not configured - free-text "
            "semantic search needs an embedding model."
        )
    if not texts:
        return []

    settings = get_settings()
    url, headers = _endpoint()
    vectors: list[list[float]] = []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            payload = {
                "input": batch,
                # Explicit rather than defaulted: the stored vectors and
                # the Atlas index are both fixed at this width, and a
                # silent change would produce vectors the index rejects.
                "dimensions": settings.azure_embedding_dimensions,
            }
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(
                    f"embedding request failed: {type(exc).__name__}"
                ) from exc

            if response.status_code != 200:
                raise EmbeddingUnavailable(
                    f"embedding endpoint returned HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            body = response.json()
            # Azure does not guarantee ordering, but it does return an
            # index per item - sorting by it is what keeps vectors
            # aligned with the products they belong to.
            ordered = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
            vectors.extend(item["embedding"] for item in ordered)

    if len(vectors) != len(texts):
        raise EmbeddingUnavailable(
            f"asked for {len(texts)} embeddings, received {len(vectors)}"
        )
    return vectors


async def embed_query(text: str) -> list[float]:
    """One string, for a search query."""
    return (await embed_texts([text]))[0]


def product_text(product: dict) -> str:
    """The text a product is embedded FROM.

    Must stay identical between the re-embed script and anything that
    reasons about it, or documents and queries drift apart. Name first
    because it carries the most signal; description and tags add the
    vocabulary that makes "cosy" or "gift" land on something.
    """
    # tags and searchKeywords frequently hold the SAME values, which
    # would otherwise repeat every keyword twice in the embedded text.
    # Repetition adds no information and drags the vector toward those
    # terms, so keyword-heavy products would drift together for no
    # reason. Deduped while preserving order, since order carries
    # emphasis - name first is deliberate.
    keywords = []
    seen = set()
    for word in (product.get("tags") or []) + (product.get("searchKeywords") or []):
        cleaned = (word or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            keywords.append(cleaned)

    parts = [
        product.get("name") or "",
        product.get("category") or "",
        product.get("subCategory") or "",
        product.get("description") or "",
        " ".join(keywords),
    ]
    return " ".join(p.strip() for p in parts if p and p.strip())[:8000]

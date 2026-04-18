"""UPC Product Lookup Service.

Uses upcitemdb.com free trial API to look up product info by UPC barcode.
All functions are non-throwing — errors are captured in UPCLookupResult.
"""

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

LOOKUP_TIMEOUT = 15  # seconds
UPCITEMDB_URL = "https://api.upcitemdb.com/prod/trial/lookup"


@dataclass
class UPCLookupResult:
    """Result of a UPC product lookup."""

    success: bool = False
    product_title: str | None = None
    brand: str | None = None
    asins: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    description: str | None = None
    error: str | None = None


async def lookup_upc(upc_code: str) -> UPCLookupResult:
    """Look up product info by UPC barcode via upcitemdb.com.

    Returns product title, brand, ASINs, and image URLs.
    Free tier: 100 lookups/day, no API key needed.
    """
    if not upc_code or not upc_code.strip():
        return UPCLookupResult(error="UPC code is required")

    upc_code = upc_code.strip()

    try:
        async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT) as client:
            resp = await client.get(UPCITEMDB_URL, params={"upc": upc_code})
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            if not items:
                return UPCLookupResult(error=f"No product found for UPC {upc_code}")

            item = items[0]

            # Extract ASINs from offers
            asins = []
            for offer in item.get("offers", []):
                asin = offer.get("asin")
                if asin and asin not in asins:
                    asins.append(asin)

            # Also check the top-level asin field
            top_asin = item.get("asin")
            if top_asin and top_asin not in asins:
                asins.insert(0, top_asin)

            result = UPCLookupResult(
                success=True,
                product_title=item.get("title"),
                brand=item.get("brand"),
                asins=asins,
                images=item.get("images", []),
                description=item.get("description"),
            )

            logger.info(
                f"UPC {upc_code}: found '{result.product_title}' "
                f"({len(result.asins)} ASINs, {len(result.images)} images)"
            )
            return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            msg = "UPC lookup rate limit exceeded (100/day). Try again tomorrow."
        else:
            msg = f"UPC lookup failed: HTTP {e.response.status_code}"
        logger.warning(msg)
        return UPCLookupResult(error=msg)
    except httpx.RequestError as e:
        msg = f"UPC lookup network error: {e}"
        logger.warning(msg)
        return UPCLookupResult(error=msg)


def compute_match_confidence(product_title: str | None, detected_title: str | None) -> str:
    """Compare UPC product title against detected disc title.

    Returns "high", "low", or "none".
    """
    if not product_title or not detected_title:
        return "none"

    prod_lower = product_title.lower()
    det_lower = detected_title.lower()

    # Direct substring match
    if det_lower in prod_lower or prod_lower in det_lower:
        return "high"

    # Word overlap check
    prod_words = set(prod_lower.split())
    det_words = set(det_lower.split())
    # Remove common filler words
    filler = {"the", "a", "an", "of", "and", "&", "-", ":", "season", "disc"}
    prod_words -= filler
    det_words -= filler

    if not prod_words or not det_words:
        return "none"

    overlap = prod_words & det_words
    ratio = len(overlap) / min(len(prod_words), len(det_words))

    if ratio >= 0.5:
        return "high"
    if ratio > 0:
        return "low"
    return "none"

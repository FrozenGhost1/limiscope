import os
import time
from typing import Any
import asyncio
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="LimiScope API", version="0.2.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])

ROBLOX_SEARCH = "https://catalog.roblox.com/v1/search/items/details"
ROBLOX_ITEM_DETAILS = "https://catalog.roblox.com/v1/catalog/items/details"
ROBLOX_RESALE = "https://economy.roblox.com/v1/assets/{asset_id}/resale-data"
ROBLOX_ECONOMY_DETAILS = "https://economy.roblox.com/v2/assets/{asset_id}/details"
ROBLOX_OWNERS = "https://inventory.roblox.com/v2/assets/{asset_id}/owners"
ROBLOX_THUMBNAILS = "https://thumbnails.roblox.com/v1/assets"
cache: dict[str, tuple[float, Any]] = {}
TTL = int(os.getenv("CACHE_TTL_SECONDS", "90"))

async def get_json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=18, headers={"User-Agent": "LimiScope/0.2"}) as client:
        response = await client.get(url, params=params)
        if response.status_code == 429:
            raise HTTPException(503, "Roblox is rate-limiting requests. Please retry shortly.")
        if response.status_code >= 400:
            raise HTTPException(502, f"Roblox API returned HTTP {response.status_code}.")
        return response.json()

async def post_json(url: str, payload: dict):
    async with httpx.AsyncClient(timeout=18, headers={"User-Agent": "LimiScope/0.2", "Content-Type": "application/json"}) as client:
        response = await client.post(url, json=payload)
        # Retry read-only catalog detail requests with Roblox's CSRF token when requested.
        if response.status_code == 403 and response.headers.get("x-csrf-token"):
            headers = {"User-Agent": "LimiScope/0.2", "Content-Type": "application/json", "x-csrf-token": response.headers["x-csrf-token"]}
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code == 429:
            raise HTTPException(503, "Roblox is rate-limiting requests. Please retry shortly.")
        if response.status_code >= 400:
            raise HTTPException(502, f"Roblox catalog details returned HTTP {response.status_code}.")
        return response.json()

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "LimiScope API", "version": "0.2.0"}

@app.get("/api/catalog")
async def catalog(q: str = "", limit: int = Query(28, ge=10, le=30), cursor: str | None = None, sort: int = Query(2, ge=0, le=5)):
    key = f"roblox-only:{q}:{limit}:{cursor or ''}:{sort}"
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    # Roblox's own user account is creator user ID 1. CreatorType=1 means User.
    params = {"Category": 2, "CreatorType": 1, "CreatorTargetId": 1, "Limit": limit, "SortType": sort, "SortAggregation": 5}
    if q.strip():
        params["Keyword"] = q.strip()
    if cursor:
        params["Cursor"] = cursor
    data = await get_json(ROBLOX_SEARCH, params)
    rows = data.get("data", [])
    ids = [str(row.get("id")) for row in rows if row.get("id")]
    thumb_data = await get_json(ROBLOX_THUMBNAILS, {"assetIds": ",".join(ids), "size": "150x150", "format": "Png", "isCircular": "false"}) if ids else {"data": []}
    thumbs = {str(x.get("targetId")): x.get("imageUrl") for x in thumb_data.get("data", [])}
    items = []
    for row in rows:
        asset_id = str(row.get("id", ""))
        creator = row.get("creator") or {}
        items.append({
            "assetId": asset_id, "name": row.get("name") or "Unnamed item",
            "description": row.get("description") or "",
            "price": row.get("price"), "lowestResalePrice": row.get("lowestResalePrice", row.get("lowestPrice")),
            "creatorName": row.get("creatorName") or creator.get("name") or "Roblox",
            "creatorId": row.get("creatorTargetId") or creator.get("id") or 1,
            "creatorType": row.get("creatorType") or "User",
            "itemType": row.get("itemType") or "Asset",
            "collectibleItemId": row.get("collectibleItemId"),
            "totalQuantity": row.get("totalQuantity"), "unitsAvailable": row.get("unitsAvailableForConsumption"),
            "favoriteCount": row.get("favoriteCount"), "itemRestrictions": row.get("itemRestrictions") or [],
            "isOffSale": row.get("isOffSale"), "imageUrl": thumbs.get(asset_id)
        })
    result = {"items": items, "nextPageCursor": data.get("nextPageCursor"), "source": "Roblox Catalog API · Roblox creator only", "observedAt": int(now)}
    cache[key] = (now + TTL, result)
    return result

@app.get("/api/items/{asset_id}")
async def item_details(asset_id: int):
    key = f"item-details:{asset_id}"
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    details_data = await post_json(ROBLOX_ITEM_DETAILS, {"items": [{"itemType": "Asset", "id": asset_id}]})
    detail_rows = details_data.get("data", [])
    if not detail_rows:
        raise HTTPException(404, "Roblox did not return details for this item.")
    item = detail_rows[0]
    # Verify the creator filter again so manually entered IDs cannot masquerade as Roblox-created.
    if str(item.get("creatorTargetId")) != "1" or item.get("creatorType") != "User":
        raise HTTPException(404, "This item is not listed as created by the Roblox user account.")
    resale, economy, owners, thumb = await asyncio.gather(
        get_json(ROBLOX_RESALE.format(asset_id=asset_id)),
        get_json(ROBLOX_ECONOMY_DETAILS.format(asset_id=asset_id)),
        get_json(ROBLOX_OWNERS.format(asset_id=asset_id), {"limit": 10, "sortOrder": "Desc"}),
        get_json(ROBLOX_THUMBNAILS, {"assetIds": str(asset_id), "size": "420x420", "format": "Png", "isCircular": "false"})
    , return_exceptions=True)
    def safe(value):
        return value if isinstance(value, dict) else {}
    thumb_rows = safe(thumb).get("data", [])
    result = {
        "assetId": asset_id, "name": item.get("name") or safe(economy).get("Name") or "Roblox limited",
        "description": item.get("description") or safe(economy).get("Description") or "",
        "creatorName": item.get("creatorName") or "Roblox", "creatorId": item.get("creatorTargetId"),
        "price": item.get("price"), "lowestResalePrice": item.get("lowestResalePrice", item.get("lowestPrice")),
        "recentAveragePrice": safe(resale).get("recentAveragePrice"), "sales": safe(resale).get("sales"),
        "volume": safe(resale).get("volume"), "originalPrice": safe(resale).get("originalPrice"),
        "totalQuantity": item.get("totalQuantity") or safe(economy).get("Remaining"),
        "favoriteCount": item.get("favoriteCount"), "itemRestrictions": item.get("itemRestrictions") or [],
        "collectibleItemId": item.get("collectibleItemId"), "imageUrl": thumb_rows[0].get("imageUrl") if thumb_rows else None,
        "owners": safe(owners).get("data", []), "ownersAvailable": isinstance(owners, dict),
        "source": "Roblox Catalog, Economy, Inventory and Thumbnails APIs", "observedAt": int(now)
    }
    cache[key] = (now + TTL, result)
    return result

@app.get("/api/items/{asset_id}/resale")
async def resale(asset_id: int):
    data = await get_json(ROBLOX_RESALE.format(asset_id=asset_id))
    return {"assetId": asset_id, "price": data.get("price"), "recentAveragePrice": data.get("recentAveragePrice"), "originalPrice": data.get("originalPrice"), "sales": data.get("sales"), "volume": data.get("volume"), "source": "Roblox Economy resale-data API", "observedAt": int(time.time())}

@app.get("/api/safety/check/{asset_id}")
async def safety_check(asset_id: int):
    return {"assetId": asset_id, "classification": "Unable to verify", "verified": False, "note": "No authoritative stolen-item registry is connected. This result cannot confirm that an item is safe or stolen.", "source": None, "observedAt": int(time.time())}


@app.get("/api/users/lookup")
async def user_lookup(user: str):
    key = f"user-lookup:{user.lower()}"
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    if user.isdigit():
        user_id = int(user)
        profile = await get_json(f"https://users.roblox.com/v1/users/{user_id}")
    else:
        lookup = await post_json("https://users.roblox.com/v1/usernames/users", {
            "usernames": [user], "excludeBannedUsers": True
        })
        matches = lookup.get("data", [])
        if not matches:
            raise HTTPException(404, "Account not found.")
        user_id = matches[0]["id"]
        profile = await get_json(f"https://users.roblox.com/v1/users/{user_id}")
    result = {
        "userId": user_id,
        "username": profile.get("name"),
        "displayName": profile.get("displayName"),
        "created": profile.get("created"),
        "description": profile.get("description")
    }
    cache[key] = (now + TTL, result)
    return result


@app.get("/api/users/{user_id}/collectibles")
async def user_collectibles(user_id: int, limit: int = Query(100, ge=10, le=100), cursor: str | None = None):
    key = f"user-collectibles:{user_id}:{limit}:{cursor or ''}"
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    try:
        data = await get_json(
            f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles",
            {"limit": limit, "sortOrder": "Desc", "cursor": cursor}
        )
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            return {
                "userId": user_id, "isPublic": False, "count": None,
                "items": [], "nextPageCursor": None,
                "note": "Inventory is private or unavailable."
            }
        raise
    rows = data.get("data", [])
    items = [{
        "assetId": str(row.get("assetId") or row.get("userAssetId") or ""),
        "name": row.get("name") or "Collectible",
        "recentAveragePrice": row.get("recentAveragePrice"),
        "serialNumber": row.get("serialNumber"),
        "originalPrice": row.get("originalPrice")
    } for row in rows]
    result = {
        "userId": user_id, "isPublic": True, "count": len(items),
        "items": items, "nextPageCursor": data.get("nextPageCursor"),
        "source": "Public collectibles inventory endpoint",
        "observedAt": int(now)
    }
    cache[key] = (now + TTL, result)
    return result

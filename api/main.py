import os
import time
from typing import Any
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title='LimiScope API', version='0.1.0')
origins = [x.strip() for x in os.getenv('CORS_ORIGINS', '*').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=['GET'], allow_headers=['*'])
ROBLOX_SEARCH = 'https://catalog.roblox.com/v1/search/items/details'
ROBLOX_RESALE = 'https://economy.roblox.com/v1/assets/{asset_id}/resale-data'
cache: dict[str, tuple[float, Any]] = {}
TTL = int(os.getenv('CACHE_TTL_SECONDS', '60'))

async def get_json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=15, headers={'User-Agent': 'LimiScope/0.1'}) as client:
        response = await client.get(url, params=params)
        if response.status_code == 429:
            raise HTTPException(503, 'Roblox is rate-limiting requests. Please retry shortly.')
        if response.status_code >= 400:
            raise HTTPException(502, f'Roblox API returned HTTP {response.status_code}.')
        return response.json()

@app.get('/api/health')
async def health():
    return {'ok': True, 'service': 'LimiScope API', 'version': '0.1.0'}

@app.get('/api/catalog')
async def catalog(q: str = '', limit: int = Query(30, ge=1, le=30), cursor: str | None = None):
    key = f'catalog:{q}:{limit}:{cursor or ""}'
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    params = {'Category': 'Collectibles', 'Limit': limit, 'Keyword': q or None, 'SortType': '3'}
    if cursor:
        params['Cursor'] = cursor
    data = await get_json(ROBLOX_SEARCH, params)
    items = []
    for row in data.get('data', []):
        creator = row.get('creator') or {}
        items.append({'assetId': str(row.get('id', '')), 'name': row.get('name') or 'Unnamed item', 'price': row.get('price'), 'lowestResalePrice': row.get('lowestPrice'), 'creatorName': creator.get('name'), 'creatorId': creator.get('id'), 'itemType': row.get('itemType'), 'productId': row.get('productId')})
    result = {'items': items, 'nextPageCursor': data.get('nextPageCursor'), 'source': 'Roblox Catalog API', 'observedAt': int(now)}
    cache[key] = (now + TTL, result)
    return result

@app.get('/api/items/{asset_id}/resale')
async def resale(asset_id: int):
    key = f'resale:{asset_id}'
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    data = await get_json(ROBLOX_RESALE.format(asset_id=asset_id))
    result = {'assetId': asset_id, 'price': data.get('price'), 'recentAveragePrice': data.get('recentAveragePrice'), 'originalPrice': data.get('originalPrice'), 'sales': data.get('sales'), 'volume': data.get('volume'), 'source': 'Roblox Economy resale-data API', 'observedAt': int(now)}
    cache[key] = (now + TTL, result)
    return result

@app.get('/api/safety/check/{asset_id}')
async def safety_check(asset_id: int):
    return {'assetId': asset_id, 'classification': 'Unable to verify', 'verified': False, 'unverifiedReportCount': 0, 'note': 'No authoritative stolen-item registry is connected. This result cannot confirm that an item is safe or stolen.', 'source': None, 'observedAt': int(time.time())}

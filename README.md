# LimiScope

Roblox limited-item market research and tracking.

## Architecture
- `index.html`: static frontend hosted by GitHub Pages.
- `api/main.py`: FastAPI service for source-backed catalog and resale lookups.
- `render.yaml`: starter deployment configuration for Render.

## API deployment
Deploy this repository as a Python web service using the included `render.yaml`, or configure:
- Build: `pip install -r requirements.txt`
- Start: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`

GitHub Pages does not execute Python. After deploying the API, configure the frontend to use its HTTPS address.

## Endpoints
- `GET /api/health`
- `GET /api/catalog?q=&limit=30&cursor=`
- `GET /api/items/{asset_id}/resale`
- `GET /api/safety/check/{asset_id}`

## Data integrity
Only fields returned by Roblox are displayed. The API does not invent RAP, value, demand, sales history, or theft status. The safety endpoint reports unverified because no authoritative theft registry is connected. Requests are briefly cached and Roblox may rate-limit them.

## Future account connection
A future account-value feature should use an official, user-authorized OAuth flow where available, request minimum read-only scopes, and never ask users for passwords, session cookies, or authentication tokens. Inventory privacy and API access limitations must be handled explicitly. No account connection is implemented yet.

## Security
LimiScope is informational and does not execute trades.

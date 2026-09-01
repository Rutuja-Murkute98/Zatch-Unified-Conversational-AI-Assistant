"""
DEV-ONLY TOOL - NOT part of the deployed app. Generates a test JWT so
you can demo the /chat endpoint while waiting for the real Zatch
backend signing secret (Phase 3.1). The moment the real secret arrives,
update .env's JWT_SECRET and this becomes unnecessary - the app itself
NEVER issues tokens, only verifies them, matching the real production
design where the main Zatch backend is the only token issuer.
"""

import asyncio

import sys
from pathlib import Path

# Dev script lives in scripts/, so the project root must be on sys.path
# for `import app` to resolve when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.db.connection import close_mongo_connection, connect_to_mongo, get_database
from app.security.auth import create_test_token


async def main():
    await connect_to_mongo()
    db = get_database()
    order = await db.orders.find_one({})
    user_id = str(order["buyerId"])
    await close_mongo_connection()

    token = create_test_token(user_id=user_id, expires_in_minutes=120)
    print(f"Test user_id: {user_id}")
    print(f"\nTest token (valid 2 hours):\n{token}")
    print("\nPaste this into the 'Authorize' button at http://127.0.0.1:8000/docs")


asyncio.run(main())
import asyncio, sys, os
sys.path.insert(0, '.')

async def main():
    from server.database import init_db, AsyncSessionLocal
    from server.models import User, APICall, FeatureRequest
    from server import main as srv
    from sqlalchemy import select, func
    import httpx

    sent = []
    class FakeResp:
        def json(self): return {"ok": True, "result": {"message_id": 1}}
        status_code = 200
        text = ""
    async def fake_post(self, url, **kw):
        sent.append((url, kw.get("json") or kw.get("data")))
        return FakeResp()
    async def fake_get(self, url, **kw):
        return FakeResp()
    httpx.AsyncClient.post = fake_post
    httpx.AsyncClient.get = fake_get

    await init_db()

    # Test 1: Submit a feature request
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/feature I want Stew to generate PowerPoint slides automatically"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"1. /feature submit: {'PASS' if 'Feature request logged' in msg else 'FAIL'}")

    # Test 2: Submit another feature request (different category)
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 2,
            "message": {
                "message_id": 2,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/feature Add Yoruba and Igbo language support"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"2. /feature language: {'PASS' if 'language' in msg.lower() or 'Feature request logged' in msg else 'FAIL'}")

    # Test 3: Submit duplicate (should merge vote)
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 3,
            "message": {
                "message_id": 3,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/feature I want Stew to generate PowerPoint slides automatically"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"3. /feature duplicate: {'PASS' if 'merge' in msg.lower() or 'similar' in msg.lower() else 'FAIL'}")

    # Test 4: A different user votes for the first feature
    async with AsyncSessionLocal() as db:
        sent.clear()
        # First get the feature ID
        fr_result = await db.execute(select(FeatureRequest).limit(1))
        fr = fr_result.scalar_one_or_none()
        fr_id = fr.id[:8] if fr else "xxxx"

        update = {
            "update_id": 4,
            "message": {
                "message_id": 4,
                "chat": {"id": 777},
                "from": {"id": 888, "username": "voter", "is_bot": False, "first_name": "Voter"},
                "text": f"/vote #{fr_id}"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"4. /vote: {'PASS' if 'Voted' in msg or 'votes' in msg else 'FAIL'}")

    # Test 5: List features
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 5,
            "message": {
                "message_id": 5,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/features"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"5. /features list: {'PASS' if 'Top Feature' in msg or 'votes' in msg else 'FAIL'}")

    # Test 6: Admin API endpoint
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FeatureRequest))
        all_features = result.scalars().all()
        print(f"6. FeatureRequest count in DB: {len(all_features)} {'PASS' if len(all_features) >= 1 else 'FAIL'}")

    print("\n=== FEATURE TRACKING TESTS DONE ===")

asyncio.run(main())

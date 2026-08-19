import asyncio, sys, os
sys.path.insert(0, '.')

async def main():
    from server.database import init_db, AsyncSessionLocal
    from server.models import User, APICall
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

    # Test 1: Admin unlock with wrong code
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/admin wrongcode"
            }
        }
        await srv._handle_telegram_update(update, db)
        print(f"1. Wrong admin code: {sent[-1][1] if sent else 'FAIL'}")

    # Test 2: Admin unlock with correct code
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 2,
            "message": {
                "message_id": 2,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/admin EROGIAN-2026-SECRET"
            }
        }
        await srv._handle_telegram_update(update, db)
        print(f"2. Correct admin code: {sent[-1][1] if sent else 'FAIL'}")
        u = (await db.execute(select(User).where(User.email == "tg_999@telegram.stew"))).scalar_one_or_none()
        print(f"   Plan after unlock: {u.plan}")

    # Test 3: /usage as owner
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 3,
            "message": {
                "message_id": 3,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/usage"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"3. /usage owner: {'PASS' if 'Unlimited' in msg else 'FAIL'}")

    # Test 4: /users
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 4,
            "message": {
                "message_id": 4,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/users"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"4. /users: {'PASS' if 'people are using' in msg else 'FAIL'}")

    # Test 5: /start shows user count
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 5,
            "message": {
                "message_id": 5,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/start"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"5. /start user count: {'PASS' if 'people are using' in msg else 'FAIL'}")

    # Test 6: /upgrade
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 6,
            "message": {
                "message_id": 6,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/upgrade"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"6. /upgrade: {'PASS' if 'Pro' in msg and 'Business' in msg else 'FAIL'}")

    # Test 7: Quota limit reached for free user
    async with AsyncSessionLocal() as db:
        from server.auth import generate_api_key
        u2 = User(name="LimitTest", email="tg_888@telegram.stew", plan="free", api_key=generate_api_key())
        db.add(u2)
        await db.flush()
        await db.refresh(u2)
        for i in range(1500):
            db.add(APICall(user_id=u2.id, endpoint="/telegram/message", method="POST", tokens_used=0, status_code=200))
        await db.commit()

    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 7,
            "message": {
                "message_id": 7,
                "chat": {"id": 777},
                "from": {"id": 888, "username": "limituser", "is_bot": False, "first_name": "Limit"},
                "text": "research quantum physics"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "nothing"
        is_blocked = "limit" in msg.lower() or "upgrade" in msg.lower()
        print(f"7. Quota limit blocked: {'PASS' if is_blocked else 'FAIL'}")

    # Test 8: /plan command
    async with AsyncSessionLocal() as db:
        sent.clear()
        update = {
            "update_id": 8,
            "message": {
                "message_id": 8,
                "chat": {"id": 555},
                "from": {"id": 999, "username": "testuser", "is_bot": False, "first_name": "Test"},
                "text": "/plan"
            }
        }
        await srv._handle_telegram_update(update, db)
        msg = str(sent[-1][1]) if sent else "FAIL"
        print(f"8. /plan: {'PASS' if 'Free' in msg and 'Pro' in msg and 'Business' in msg else 'FAIL'}")

    # Test 9: _get_telegram_user_count
    async with AsyncSessionLocal() as db:
        count = await srv._get_telegram_user_count(db)
        print(f"9. User count: {count} (expect 2) {'PASS' if count == 2 else 'FAIL'}")

    print("\n=== ALL TESTS DONE ===")

asyncio.run(main())

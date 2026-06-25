with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

checks = {
    "function showPage(": 1,
    "function openStorePage": 1,
    "function openStewPage": 1,
    "function openSlimeCode": 1,
    "function renderSlimeStore": 1,
    "function handleSignedIn": 1,
    "function loadUserPlanAndCoins": 1,
    "function handleStewTask": 1,
    "async function runStewTask": 1,
    "async function scGenerate": 1,
    "function scRun": 1,
    "function scRefreshPreview": 1,
    "showPage_sc": 0,  # should be removed
    "_origHandleSignedIn": 0,
    "_origLoad": 0,
    "_origShowPage": 0,
    "slimecodeNav": 0,
}

print("=== FINAL VERIFICATION ===")
all_good = True
for pattern, expected in checks.items():
    count = content.count(pattern)
    if count == expected:
        print(f"✅ {pattern}: {count}")
    else:
        print(f"❌ {pattern}: {count} (expected {expected})")
        all_good = False

# Check that the Mistral fallback is in runStewTask
if "Mistral AI directly as STEW" in content:
    print("✅ STEW has Mistral fallback")
else:
    print("❌ STEW missing Mistral fallback")
    all_good = False

# Check back buttons
back_count = content.count("showPage('chat')")
print(f"✅ Back buttons (showPage('chat')): {back_count}")

print(f"\n{'🎉 ALL CHECKS PASSED!' if all_good else '⚠️ Issues remain'}")
print(f"File: {len(content)} chars, {content.count(chr(10))} lines")

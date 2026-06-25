with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# Add STEW_URL before the stew functions
old = "// ── STEW AGENT ──\nasync function runStewTask"
new = "// ── STEW AGENT ──\nconst STEW_URL = 'https://stew-ai.onrender.com'; // Update with your Render URL\nasync function runStewTask"
if old in content:
    content = content.replace(old, new)
    print("✅ Added STEW_URL constant")
else:
    print("❌ Could not find STEW AGENT comment")

# Check for the second verifyPaystackPayment - does it have the full plan handling?
# Let me check if applyPlanTheme is called in the verifyPaystackPayment we kept
if "applyPlanTheme(planId)" in content and "verifyPaystackPayment" in content:
    print("✅ applyPlanTheme is present in the file")
else:
    print("⚠️ applyPlanTheme may be missing from verifyPaystackPayment")

# Let me also check for duplicate verifyPaystackPayment in the fixed file
count = content.count("async function verifyPaystackPayment")
print(f"verifyPaystackPayment count: {count}")

count = content.count("function buyCoins")
print(f"buyCoins count: {count}")

count = content.count("function buyPlan")
print(f"buyPlan count: {count}")

count = content.count("function renderSlimeStore")
print(f"renderSlimeStore count: {count}")

count = content.count("function loadUserPlanAndCoins")
print(f"loadUserPlanAndCoins count: {count}")

count = content.count("function handleSignedIn")
print(f"handleSignedIn count: {count}")

count = content.count("function startCall")
print(f"startCall count: {count}")

count = content.count("function endCall")
print(f"endCall count: {count}")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

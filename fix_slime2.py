with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    lines = f.readlines()

# Find line numbers (0-indexed) for the blocks to remove
def find_line(text, start_from=0):
    for i in range(start_from, len(lines)):
        if text in lines[i]:
            return i
    return -1

# FIX 1b: Remove the second handleSignedIn block (lines ~3383-3407)
# From "const _origHandleSignedIn" to the closing "}" before "// ══ FULL PREMIUM THEME ENGINE"
start = find_line("const _origHandleSignedIn")
if start >= 0:
    # Find the end - the "}" followed by blank line and "// ══ FULL PREMIUM THEME ENGINE"
    end = -1
    for i in range(start+1, len(lines)):
        if "// ══ FULL PREMIUM THEME ENGINE" in lines[i]:
            end = i
            break
    if end > 0:
        # Remove from start to end (exclusive), keeping the FULL PREMIUM THEME ENGINE comment
        del lines[start:end]
        print(f"✅ FIX 1b: Removed recursive handleSignedIn wrapper (lines {start+1}-{end})")
    else:
        print("❌ FIX 1b: Could not find end marker")
else:
    print("❌ FIX 1b: Could not find _origHandleSignedIn")

# FIX 3: Remove first renderSlimeStore and SLIME_STORE_APPS
start = find_line("const SLIME_STORE_APPS")
if start >= 0:
    # Find the end - the next "function renderSlimeStore" (the second/better one) or the "STORE_APPS" section
    end = -1
    for i in range(start+1, len(lines)):
        if "// ══ SLIME STORE — FULL APP LIST ══" in lines[i]:
            end = i
            break
    if end > 0:
        del lines[start:end]
        print(f"✅ FIX 3: Removed first SLIME_STORE_APPS + renderSlimeStore (lines {start+1}-{end})")
    else:
        print("❌ FIX 3: Could not find end marker for first store section")
else:
    print("❌ FIX 3: Could not find SLIME_STORE_APPS")

# FIX 4: Remove first buyCoins, buyPlan, verifyPaystackPayment
# Find first "function buyCoins" 
start = find_line("function buyCoins(id, name, coins, price)")
if start >= 0:
    # Find the end - the second buyCoins (marked by "// ══ PAYSTACK LIVE INTEGRATION ══")
    end = -1
    for i in range(start+1, len(lines)):
        if "// ══ PAYSTACK LIVE INTEGRATION ══" in lines[i]:
            end = i
            break
    if end > 0:
        del lines[start:end]
        print(f"✅ FIX 4: Removed first buyCoins/buyPlan/verifyPaystackPayment (lines {start+1}-{end})")
    else:
        print("❌ FIX 4: Could not find end marker for first payment section")
else:
    print("❌ FIX 4: Could not find first buyCoins")

# FIX 5: Remove first startCall and endCall (keep the second ones with wave animation)
# The first startCall is "async function startCall(){" at ~line 2486
start = find_line("async function startCall(){")
if start >= 0:
    # Find the end - the line before "function startCallListen" 
    end = -1
    for i in range(start+1, len(lines)):
        if "function startCallListen(){" in lines[i]:
            end = i
            break
    if end > 0:
        del lines[start:end]
        print(f"✅ FIX 5a: Removed first startCall (lines {start+1}-{end})")
    else:
        print("❌ FIX 5a: Could not find startCallListen")
else:
    print("❌ FIX 5a: Could not find first startCall")

# Now remove first endCall
start = find_line("function endCall(){")
if start >= 0:
    # Find the end - the line before "async function callSpeak"
    end = -1
    for i in range(start+1, len(lines)):
        if "async function callSpeak" in lines[i]:
            end = i
            break
    if end > 0:
        del lines[start:end]
        print(f"✅ FIX 5b: Removed first endCall (lines {start+1}-{end})")
    else:
        print("❌ FIX 5b: Could not find callSpeak")
else:
    print("❌ FIX 5b: Could not find first endCall")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.writelines(lines)

print(f"\nFile now has {len(lines)} lines (was 4165)")

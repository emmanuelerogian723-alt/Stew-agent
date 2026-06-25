with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# Add back buttons to STEW page
old_stew_header = """<div id="stewPage" class="page" style="display:none;padding:1rem;overflow-y:auto;height:100%;">
  <div style="text-align:center;padding:1rem 0 1.5rem;">
    <div style="font-size:2rem">🤖</div>
    <div style="font-size:1.4rem;font-weight:700;color:var(--accent)">STEW Agent</div>
    <div style="font-size:.85rem;opacity:.6;margin-top:.25rem">100 AI agents working for you</div>
  </div>"""

new_stew_header = """<div id="stewPage" class="page" style="display:none;padding:1rem;overflow-y:auto;height:100%;">
  <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.75rem;">
    <button onclick="showPage('chat')" style="background:rgba(0,200,255,.1);border:1px solid var(--accent3);color:var(--accent);border-radius:10px;padding:.4rem .7rem;font-size:.8rem;cursor:pointer;">← Back</button>
  </div>
  <div style="text-align:center;padding:.5rem 0 1.5rem;">
    <div style="font-size:2rem">🤖</div>
    <div style="font-size:1.4rem;font-weight:700;color:var(--accent)">STEW Agent</div>
    <div style="font-size:.85rem;opacity:.6;margin-top:.25rem">100 AI agents working for you</div>
  </div>"""

if old_stew_header in content:
    content = content.replace(old_stew_header, new_stew_header)
    print("✅ Added Back button to STEW page")
else:
    print("❌ Could not find stew page header")

# Add back button to Slime Code page
old_sc_header = """<div id="slimecodePage" class="page" style="display:none;padding:0;height:calc(100vh - 56px);flex-direction:column;">
  <div style="background:rgba(0,0,0,.5);border-bottom:1px solid rgba(0,200,255,.15);padding:.65rem 1rem;display:flex;align-items:center;gap:.6rem;flex-shrink:0;">"""

new_sc_header = """<div id="slimecodePage" class="page" style="display:none;padding:0;height:calc(100vh - 56px);flex-direction:column;">
  <div style="background:rgba(0,0,0,.5);border-bottom:1px solid rgba(0,200,255,.15);padding:.65rem 1rem;display:flex;align-items:center;gap:.6rem;flex-shrink:0;">
    <button onclick="showPage('chat')" style="background:rgba(0,200,255,.1);border:1px solid var(--accent3);color:var(--accent);border-radius:8px;padding:.3rem .6rem;font-size:.75rem;cursor:pointer;flex-shrink:0;">← Back</button>"""

if old_sc_header in content:
    content = content.replace(old_sc_header, new_sc_header)
    print("✅ Added Back button to Slime Code page")
else:
    print("❌ Could not find slime code page header")

# Add back button to Store page
old_store_header = """<div id="storePage" class="page" style="display:none;padding:1rem;overflow-y:auto;height:100%;">
  <div style="text-align:center;padding:1rem 0 1.5rem;">
    <div style="font-size:2rem">🏪</div>
    <div style="font-size:1.4rem;font-weight:700;color:var(--accent)">Slime Store</div>"""

new_store_header = """<div id="storePage" class="page" style="display:none;padding:1rem;overflow-y:auto;height:100%;">
  <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.75rem;">
    <button onclick="showPage('chat')" style="background:rgba(0,200,255,.1);border:1px solid var(--accent3);color:var(--accent);border-radius:10px;padding:.4rem .7rem;font-size:.8rem;cursor:pointer;">← Back</button>
  </div>
  <div style="text-align:center;padding:.5rem 0 1.5rem;">
    <div style="font-size:2rem">🏪</div>
    <div style="font-size:1.4rem;font-weight:700;color:var(--accent)">Slime Store</div>"""

if old_store_header in content:
    content = content.replace(old_store_header, new_store_header)
    print("✅ Added Back button to Store page")
else:
    print("❌ Could not find store page header")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

print(f"\nFile size: {len(content)} chars")

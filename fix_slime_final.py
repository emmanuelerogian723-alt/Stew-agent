with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

store_block = """// ══ SLIME STORE — FULL APP LIST ══
const STORE_APPS = [
  {id:'cv-builder',  name:'CV Builder Pro',    desc:'Build professional CVs and resumes in minutes with AI',   cat:'Productivity', icon:'📄', url:'https://cv.mutyint.com',         premium:true},
  {id:'budget-ng',   name:'Budget NG',          desc:'Track spending and save money in Naira',                  cat:'Finance',      icon:'💰', url:'https://app.budgetng.com',        premium:true},
  {id:'study-ai',    name:'Study AI Nigeria',   desc:'AI study assistant for Nigerian university students',     cat:'Education',    icon:'📚', url:'https://studyai.ng',              premium:true},
  {id:'logo-gen',    name:'Logo Maker AI',      desc:'Create professional logos for your business instantly',   cat:'Creative',     icon:'🎨', url:'https://logoai.design',           premium:true},
  {id:'business-ng', name:'Business Name Gen',  desc:'Generate the perfect name for your Nigerian business',   cat:'Business',     icon:'🏢', url:'https://namegen.ng',              premium:true},
  {id:'social-cap',  name:'Caption Generator',  desc:'Viral captions for Instagram, TikTok and Twitter',       cat:'Creative',     icon:'✍️', url:'https://caption.ai',              premium:true},
  {id:'invoice-ng',  name:'Invoice Builder',    desc:'Create professional invoices and receipts fast',          cat:'Business',     icon:'🧾', url:'https://invoice.ng',              premium:true},
  {id:'prayer-ai',   name:'Prayer & Devotion',  desc:'Daily devotions and prayers powered by AI',              cat:'Lifestyle',    icon:'🙏', url:'https://prayer.ai',               premium:true},
  {id:'agric-ai',    name:'Agric Advisor',      desc:'AI crop and farming advice for Nigerian farmers',        cat:'Agriculture',  icon:'🌱', url:'https://agric.ng',                premium:true},
  {id:'code-ai',     name:'Code Explainer',     desc:'Paste any code and get a plain English explanation',     cat:'Developer',    icon:'💻', url:'https://codeai.dev',              premium:true},
  {id:'health-ng',   name:'Health Checker NG',  desc:'Symptom checker trained on Nigerian health data',        cat:'Health',       icon:'🏥', url:'https://health.ng',               premium:true},
  {id:'news-ng',     name:'Nigeria News AI',    desc:'Summarised daily news from all Nigerian sources',        cat:'News',         icon:'📰', url:'https://newsum.ng',               premium:true},
];
let storeCat = 'All';
let storeQuery = '';

function renderSlimeStore() {
  const container = document.getElementById('storeAppsGrid');
  if (!container) return;
  if (userPlan === 'free') {
    container.innerHTML = `
      <div class="store-locked">
        <div style="font-size:3.5rem;margin-bottom:.75rem">🔒</div>
        <div style="font-size:1.1rem;font-weight:700;color:var(--accent);margin-bottom:.5rem">Slime Store is Premium</div>
        <div style="font-size:.85rem;opacity:.65;margin-bottom:1.5rem;line-height:1.5">Unlock ${STORE_APPS.length}+ apps with any paid plan</div>
        <button class="btn-primary" onclick="showCoinModal()">Unlock Store — from ₦1,500/mo</button>
        <div style="margin-top:1.5rem;display:grid;grid-template-columns:1fr 1fr;gap:.5rem;opacity:.35;pointer-events:none;">
          ${STORE_APPS.slice(0,4).map(a=>`<div class="store-card"><div class="sc-icon">${a.icon}</div><div class="sc-name">${a.name}</div></div>`).join('')}
        </div>
      </div>`;
    return;
  }
  const cats = ['All',...new Set(STORE_APPS.map(a=>a.cat))];
  const filtered = STORE_APPS.filter(a =>
    (storeCat==='All' || a.cat===storeCat) &&
    (!storeQuery || a.name.toLowerCase().includes(storeQuery.toLowerCase()) || a.desc.toLowerCase().includes(storeQuery.toLowerCase()))
  );
  container.innerHTML = `
    <input class="store-search" placeholder="Search apps..." oninput="storeQuery=this.value;renderSlimeStore()" value="${storeQuery}">
    <div class="store-cats">
      ${cats.map(c=>`<button class="cat-btn ${storeCat===c?'active':''}" onclick="storeCat='${c}';renderSlimeStore()">${c}</button>`).join('')}
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.85rem;">
      ${filtered.map(app=>`
        <div class="store-card" onclick="openIframe('${app.url}','${app.name}')">
          <div class="sc-icon">${app.icon}</div>
          <div class="sc-name">${app.name}</div>
          <div class="sc-desc">${app.desc}</div>
          <div class="sc-cat">${app.cat}</div>
          <button class="sc-launch">Launch →</button>
        </div>`).join('')}
      ${filtered.length===0?'<div style="grid-column:1/-1;text-align:center;padding:2rem;opacity:.5">No apps found</div>':''}
    </div>`;
}

// ══ PAYSTACK LIVE INTEGRATION ══"""

old_marker = "// ══ PAYSTACK LIVE INTEGRATION ══"
if old_marker in content:
    content = content.replace(old_marker, store_block)
    print("✅ Restored STORE_APPS, storeCat, storeQuery, and renderSlimeStore")
else:
    print("❌ Could not find PAYSTACK marker")

# Final verification - check all function counts
checks = {
    "function renderSlimeStore": 1,
    "function handleSignedIn": 1,
    "function loadUserPlanAndCoins": 1,
    "function buyCoins": 1,
    "function buyPlan": 1,
    "async function verifyPaystackPayment": 1,
    "function handleStewTask": 1,
    "async function runStewTask": 1,
    "const STORE_APPS": 1,
    "const STEW_URL": 1,
}
print("\n=== FINAL VERIFICATION ===")
all_good = True
for func, expected in checks.items():
    count = content.count(func)
    status = "✅" if count == expected else "❌"
    if count != expected:
        all_good = False
    print(f"{status} {func}: {count} (expected {expected})")

# Also verify no broken patterns remain
if "_origHandleSignedIn" in content:
    print("❌ _origHandleSignedIn still present!")
    all_good = False
else:
    print("✅ _origHandleSignedIn removed")

if "_origLoad" in content:
    print("❌ _origLoad still present!")
    all_good = False
else:
    print("✅ _origLoad removed")

if "getElementById('inp')" in content:
    print("❌ getElementById('inp') still present!")
    all_good = False
else:
    print("✅ getElementById('inp') fixed")

if "getElementById('chatBox')" in content:
    print("❌ getElementById('chatBox') still present!")
    all_good = False
else:
    print("✅ getElementById('chatBox') fixed")

if "slimecodeNav" in content:
    print("❌ slimecodeNav still present!")
    all_good = False
else:
    print("✅ slimecodeNav fixed")

if "getElementById('callWaveCanvas')" in content and 'id="callWaveCanvas"' in content:
    print("✅ callWaveCanvas exists and is referenced")
else:
    print("⚠️ callWaveCanvas check - referenced: " + str("getElementById('callWaveCanvas')" in content) + ", defined: " + str('id="callWaveCanvas"' in content))

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

print(f"\nFile size: {len(content)} chars, {content.count(chr(10))} lines")
print(f"\n{'🎉 ALL FIXES VERIFIED!' if all_good else '⚠️ Some issues remain'}")

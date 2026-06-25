import re

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# ============================================================
# FIX 1: Remove the first (broken) handleSignedIn and fix the second one
# The first handleSignedIn (line ~1520) has setTimeout calls BEFORE the guard
# The second handleSignedIn (line ~3385) has infinite recursion via _origHandleSignedIn
# Solution: Replace the first handleSignedIn with a clean version, remove the second
# ============================================================

# Replace the first handleSignedIn with a fixed version (guard first, then setTimeout)
old_handleSignedIn_1 = """function handleSignedIn(user){
  setTimeout(() => checkAndSendWelcome(user), 3000);
  setTimeout(loadUserPlanAndCoins, 1000);
  if(_signedInOnce&&currentUser&&currentUser.uid===user.uid)return; // prevent duplicate
  _signedInOnce=true;currentUser=user;"""

new_handleSignedIn_1 = """function handleSignedIn(user){
  // GUARD FIRST — prevent duplicate execution before any side effects
  if(_signedInOnce&&currentUser&&currentUser.uid===user.uid)return;
  _signedInOnce=true;currentUser=user;
  // Now safe to schedule side effects
  setTimeout(() => checkAndSendWelcome(user), 3000);
  setTimeout(loadUserPlanAndCoins, 1000);"""

if old_handleSignedIn_1 in content:
    content = content.replace(old_handleSignedIn_1, new_handleSignedIn_1)
    print("✅ FIX 1a: Fixed handleSignedIn guard order (first definition)")
else:
    print("❌ FIX 1a: Could not find first handleSignedIn pattern")

# Remove the second handleSignedIn block (the _origHandleSignedIn wrapper)
# This includes: const SUPER_ADMINS, const _origHandleSignedIn, and the second handleSignedIn
old_handleSignedIn_2 = """const SUPER_ADMINS = ['multipurposetalentedyounginven@gmail.com'];
const _origHandleSignedIn = handleSignedIn;
function handleSignedIn(user) {
  _origHandleSignedIn(user);
  // Force Business plan for admin emails
  if (SUPER_ADMINS.includes(user.email)) {
    setTimeout(() => {
      userPlan = 'business';
      coinBalance = 99999;
      updateCoinDisplay();
      applyPremiumTheme('business');
      updateCoinDisplay();
      // Save to Firebase too
      if (fbDb) {
        fbDb.collection('users').doc(user.uid).set({
          plan:'business', coins:99999,
          email:user.email, name:user.displayName||'Emmanuel',
          created:new Date().toISOString(), is_admin:true
        }, {merge:true});
      }
    }, 1200);
  } else {
    setTimeout(loadUserPlanAndCoins, 1000);
  }
}"""

new_handleSignedIn_2 = """const SUPER_ADMINS = ['multipurposetalentedyounginven@gmail.com'];
// Admin override is now handled inside handleSignedIn directly (no recursion)"""

if old_handleSignedIn_2 in content:
    content = content.replace(old_handleSignedIn_2, new_handleSignedIn_2)
    print("✅ FIX 1b: Removed recursive _origHandleSignedIn wrapper")
else:
    print("❌ FIX 1b: Could not find second handleSignedIn pattern")

# Now add the admin logic to the first (now only) handleSignedIn
# Add after the Firestore save line
old_fs_save = "  if(fbDb){fbDb.collection('users').doc(user.uid).set({name,email,lastSeen:new Date().toISOString()},{merge:true}).catch(e=>HEALER.log(e));}"
new_fs_save = """  if(fbDb){fbDb.collection('users').doc(user.uid).set({name,email,lastSeen:new Date().toISOString()},{merge:true}).catch(e=>HEALER.log(e));}
  // Admin override — force Business plan for admin emails
  if (SUPER_ADMINS.includes(user.email)) {
    setTimeout(() => {
      userPlan = 'business';
      coinBalance = 99999;
      updateCoinDisplay();
      applyPlanTheme('business');
      if (fbDb) {
        fbDb.collection('users').doc(user.uid).set({
          plan:'business', coins:99999, is_admin:true
        }, {merge:true}).catch(e=>HEALER.log(e));
      }
    }, 1200);
  }"""

if old_fs_save in content:
    content = content.replace(old_fs_save, new_fs_save)
    print("✅ FIX 1c: Added admin override to handleSignedIn")
else:
    print("❌ FIX 1c: Could not find Firestore save line")

# ============================================================
# FIX 2: Remove the first (basic) loadUserPlanAndCoins, keep the second (complete) one
# Also remove the broken _origLoad line
# ============================================================

old_load_1 = """function loadUserPlanAndCoins() {
  if (!currentUser || !fbDb) return;
  fbDb.collection('users').doc(currentUser.uid).get().then(doc => {
    if (doc.exists) {
      const d = doc.data();
      userPlan = d.plan || 'free';
      coinBalance = d.coins || 0;
      updateCoinDisplay();
    }
  }).catch(e => HEALER.log(e));
}"""

new_load_1 = """// loadUserPlanAndCoins is defined later with full new-user creation logic"""

if old_load_1 in content:
    content = content.replace(old_load_1, new_load_1)
    print("✅ FIX 2a: Removed first (basic) loadUserPlanAndCoins")
else:
    print("❌ FIX 2a: Could not find first loadUserPlanAndCoins")

# Remove the broken _origLoad line
old_orig_load = """// Override loadUserPlanAndCoins to also apply theme
const _origLoad = typeof loadUserPlanAndCoins === 'function' ? loadUserPlanAndCoins : null;
function loadUserPlanAndCoins() {"""

new_orig_load = """// Full loadUserPlanAndCoins with new-user creation and theme application
function loadUserPlanAndCoins() {"""

if old_orig_load in content:
    content = content.replace(old_orig_load, new_orig_load)
    print("✅ FIX 2b: Removed broken _origLoad wrapper")
else:
    print("❌ FIX 2b: Could not find _origLoad pattern")

# ============================================================
# FIX 3: Remove the first (basic) renderSlimeStore and SLIME_STORE_APPS
# Keep the second (full) one with STORE_APPS
# ============================================================

old_store_1 = """// ── SLIME STORE ──
const SLIME_STORE_APPS = [
  { id:'cv-builder', name:'CV Builder Pro', desc:'Build professional CVs in minutes', category:'Productivity', icon:'📄', url:'https://cv.mutyint.com', premium:true },
  { id:'budget-ng',  name:'Budget NG',       desc:'Track your money in Naira',        category:'Finance',     icon:'💰', url:'https://budget.ng', premium:true },
  { id:'study-ai',   name:'Study AI',        desc:'AI-powered study assistant',       category:'Education',   icon:'📚', url:'https://studyai.app', premium:true },
  { id:'logo-gen',   name:'Logo Generator',  desc:'Create your brand logo with AI',   category:'Creative',    icon:'🎨', url:'https://logogen.ai', premium:true },
];
function renderSlimeStore() {
  const container = document.getElementById('storeAppsGrid');
  if (!container) return;
  if (userPlan === 'free') {
    container.innerHTML = \`
      <div class="store-locked">
        <div style="font-size:3.5rem;margin-bottom:.75rem">🔒</div>
        <div style="font-size:1.2rem;font-weight:700;margin:1rem 0">Slime Store is for Paid Users</div>
        <div style="font-size:.85rem;opacity:.65;margin-bottom:1rem;line-height:1.5">Upgrade to access premium apps built for Nigerian students.</div>
        <button class="btn-primary" onclick="showCoinModal()">Upgrade — from ₦1,500/mo</button>
      </div>\`;
    return;
  }
  container.innerHTML = SLIME_STORE_APPS.map(app => \`
    <div class="store-card" onclick="openIframe('${app.url}','${app.name}')">
      <div class="sc-icon">${app.icon}</div>
      <div class="sc-name">${app.name}</div>
      <div class="sc-desc">${app.desc}</div>
      <button class="sc-launch">Launch →</button>
    </div>\`).join('');
}"""

new_store_1 = """// ── SLIME STORE (full version defined later with STORE_APPS) ──"""

if old_store_1 in content:
    content = content.replace(old_store_1, new_store_1)
    print("✅ FIX 3: Removed first (basic) renderSlimeStore + SLIME_STORE_APPS")
else:
    print("❌ FIX 3: Could not find first renderSlimeStore")

# ============================================================
# FIX 4: Remove the first (basic) buyCoins, buyPlan, verifyPaystackPayment
# Keep the second (better) ones with PAYSTACK_KEY and transactions
# ============================================================

old_pay_1 = """function buyCoins(id, name, coins, price) {
  if (!currentUser) { showToast('Please sign in first'); return; }
  const handler = PaystackPop.setup({
    key: 'pk_live_b73d27d70e64ebb36f0523cb5754e77deba9080b',
    email: currentUser.email,
    amount: price * 100,
    currency: 'NGN',
    ref: 'SLIME_COIN_' + Date.now(),
    metadata: { type: 'coins', coins: coins, pack: id, uid: currentUser.uid },
    callback: function(response) {
      verifyPaystackPayment(response.reference, 'coins', coins, 0);
    },
    onClose: function() { showToast('Payment cancelled'); }
  });
  handler.openIframe();
}
function buyPlan(planId) {
  if (!currentUser) { showToast('Please sign in first'); return; }
  const plan = PLANS[planId];
  if (!plan || plan.price === 0) return;
  const handler = PaystackPop.setup({
    key: 'pk_live_b73d27d70e64ebb36f0523cb5754e77deba9080b',
    email: currentUser.email,
    amount: plan.price * 100,
    currency: 'NGN',
    ref: 'SLIME_PLAN_' + Date.now(),
    metadata: { type: 'plan', plan: planId, uid: currentUser.uid },
    callback: function(response) {
      verifyPaystackPayment(response.reference, 'plan', 0, planId);
    },
    onClose: function() { showToast('Payment cancelled'); }
  });
  handler.openIframe();
}
async function verifyPaystackPayment(ref, type, coins, planId) {
  showToast('Verifying payment...');
  try {
    if (type === 'coins') {
      coinBalance += coins;
      updateCoinDisplay();
      if (fbDb && currentUser) {
        await fbDb.collection('users').doc(currentUser.uid).update({
          coins: firebase.firestore.FieldValue.increment(coins),
          last_coin_purchase: new Date().toISOString()
        });
      }
      closeCoinModal();
      showToast('🪙 ' + coins + ' Slime Coins added!');
    } else if (type === 'plan') {
      userPlan = planId;
      if (fbDb && currentUser) {
        await fbDb.collection('users').doc(currentUser.uid).update({
          plan: planId,
          plan_started: new Date().toISOString()"""

# Need to find the exact end of the first verifyPaystackPayment
# Let me check what comes after
old_pay_1_end = """        });
      }
      applyPlanTheme(planId);
      updateCoinDisplay();
      closeCoinModal();
      showToast('🎉 Welcome to ' + PLANS[planId].name + ' Plan!');
    }
  } catch(e) {
    HEALER.log(e);
    showToast('Payment confirmed! Refresh if balance does not update.');
  }
}"""

# Actually this is getting complex. Let me take a different approach - find and remove by line numbers
print("Switching to line-number based removal for payment functions...")

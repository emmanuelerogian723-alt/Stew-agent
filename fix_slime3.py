with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# FIX 6: getElementById('inp') -> getElementById('textInput')
old = "if(checkYouTubeLink(document.getElementById('inp')?.value||'')){document.getElementById('inp').value='';autoResize();return;}"
new = "if(checkYouTubeLink(document.getElementById('textInput')?.value||'')){document.getElementById('textInput').value='';autoResize();return;}"
if old in content:
    content = content.replace(old, new)
    print("✅ FIX 6: Fixed getElementById('inp') -> 'textInput'")
else:
    print("❌ FIX 6: Could not find inp reference")

# FIX 7: getElementById('chatBox') -> getElementById('messages')
content = content.replace("getElementById('chatBox') || document.getElementById('msgs')", "document.getElementById('messages')")
print("✅ FIX 7: Fixed chatBox/msgs -> messages")

# FIX 8: Add callWaveCanvas to the call screen HTML
old_call_screen = """<div class="call-screen" id="callScreen">
  <div class="call-orb">🌊</div>"""
new_call_screen = """<div class="call-screen" id="callScreen">
  <canvas id="callWaveCanvas" style="position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;"></canvas>
  <div class="call-orb" style="position:relative;z-index:1;">🌊</div>"""
if old_call_screen in content:
    content = content.replace(old_call_screen, new_call_screen)
    print("✅ FIX 8: Added callWaveCanvas to call screen")
else:
    print("❌ FIX 8: Could not find call screen HTML")

# FIX 9: Add z-index:1 to call screen children so they're above canvas
old_call_st = '<div id="callSt" style="display:none;">'
new_call_st = '<div id="callSt" style="display:none;position:relative;z-index:1;">'
content = content.replace(old_call_st, new_call_st)

old_call_vwave = '<div class="call-vwave">'
new_call_vwave = '<div class="call-vwave" style="position:relative;z-index:1;">'
content = content.replace(old_call_vwave, new_call_vwave)

old_call_trans = '<div class="call-trans" id="callTrans">'
new_call_trans = '<div class="call-trans" id="callTrans" style="position:relative;z-index:1;">'
content = content.replace(old_call_trans, new_call_trans)

old_call_btns = '<div class="call-btns">'
new_call_btns = '<div class="call-btns" style="position:relative;z-index:1;">'
content = content.replace(old_call_btns, new_call_btns)
print("✅ FIX 9: Added z-index to call screen children")

# FIX 10: Add missing handleStewTask function
# Find the runStewTask function and add handleStewTask before it
handle_stew = """function handleStewTask() {
  const input = document.getElementById('stewInput');
  const output = document.getElementById('stewOutput');
  if (!input || !input.value.trim()) { showToast('Please enter a task for STEW'); return; }
  if (!spendCoins('stew_task')) return;
  const task = input.value.trim();
  input.value = '';
  if (output) output.innerHTML = '<div style="opacity:.5">🤖 STEW agents deploying... please wait</div>';
  runStewTask(task).then(result => {
    if (output) output.textContent = result;
  }).catch(e => {
    if (output) output.textContent = 'Error: ' + (e.message || 'STEW task failed');
    HEALER.log(e);
  });
}

"""
old_run_stew = "async function runStewTask(task) {"
if old_run_stew in content:
    content = content.replace(old_run_stew, handle_stew + old_run_stew)
    print("✅ FIX 10: Added missing handleStewTask function")
else:
    print("❌ FIX 10: Could not find runStewTask")

# FIX 11: Add nav items for Slime Store, STEW Agent, and Slime Code
old_nav = '    <div class="nav-item" id="nav-market" onclick="showPage(\'market\')"><span class="nav-ic">🛍️</span>Slime Market</div>'
new_nav = """    <div class="nav-item" id="nav-store" onclick="openStorePage()"><span class="nav-ic">🏪</span>Slime Store</div>
    <div class="nav-item" id="nav-stew" onclick="openStewPage()"><span class="nav-ic">🤖</span>STEW Agent</div>
    <div class="nav-item" id="nav-slimecode" onclick="openSlimeCode()"><span class="nav-ic">⚡</span>Slime Code</div>
    <div class="nav-item" id="nav-market" onclick="showPage('market')"><span class="nav-ic">🛍️</span>Slime Market</div>"""
if old_nav in content:
    content = content.replace(old_nav, new_nav)
    print("✅ FIX 11: Added nav items for Store, STEW, Slime Code")
else:
    print("❌ FIX 11: Could not find nav market item")

# FIX 12: Add openStorePage and openStewPage functions, and fix openSlimeCode
nav_funcs = """
function openStorePage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('storePage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-store');
  if (nav) nav.classList.add('active');
  closeNav();
  renderSlimeStore();
}
function openStewPage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('stewPage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-stew');
  if (nav) nav.classList.add('active');
  closeNav();
}

"""
# Add these functions right before openSlimeCode
old_open_slimecode = "function openSlimeCode() {"
if old_open_slimecode in content:
    content = content.replace(old_open_slimecode, nav_funcs + "function openSlimeCode() {")
    print("✅ FIX 12: Added openStorePage and openStewPage functions")
else:
    print("❌ FIX 12: Could not find openSlimeCode")

# FIX 13: Fix openSlimeCode to also set nav active
old_open_sc = """function openSlimeCode() {
  if (!checkFreeLimit('slimecode')) return;
  document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
  const p = document.getElementById('slimecodePage');
  if (p) p.style.display = 'flex';
  document.querySelectorAll('.na').forEach(n => n.classList.remove('active'));"""
new_open_sc = """function openSlimeCode() {
  if (!checkFreeLimit('slimecode')) return;
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('slimecodePage');
  if (p) { p.style.display = 'flex'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-slimecode');
  if (nav) nav.classList.add('active');"""
if old_open_sc in content:
    content = content.replace(old_open_sc, new_open_sc)
    print("✅ FIX 13: Fixed openSlimeCode to use correct nav class")
else:
    print("❌ FIX 13: Could not find openSlimeCode body")

# FIX 14: Fix showPage_sc to use correct nav class
old_sc = """  const nav = document.getElementById('slimecodeNav');
  document.querySelectorAll('.na').forEach(n=>n.classList.remove('active'));"""
new_sc = """  const nav = document.getElementById('nav-slimecode');
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));"""
if old_sc in content:
    content = content.replace(old_sc, new_sc)
    print("✅ FIX 14: Fixed showPage_sc nav reference")
else:
    print("❌ FIX 14: Could not find showPage_sc nav pattern")

# FIX 15: Fix openIframe to accept 2 args (name is optional)
old_iframe = "function openIframe(url){"
new_iframe = "function openIframe(url, name){"
if old_iframe in content:
    content = content.replace(old_iframe, new_iframe)
    print("✅ FIX 15: Fixed openIframe to accept optional name param")
else:
    print("❌ FIX 15: Could not find openIframe")

# FIX 16: Update the logo version text
content = content.replace("SLIME</span><span class=\"logo-v\">2.5 ULTRA", "SLIME</span><span class=\"logo-v\">2.6")
print("✅ FIX 16: Updated logo version to 2.6")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

print(f"\nFile size: {len(content)} chars, {content.count(chr(10))} lines")

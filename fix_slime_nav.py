with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# ============================================================
# FIX: Navigation freeze bug — inline display:none overrides CSS .active
# The problem: openStewPage/openSlimeCode/openStorePage set style.display='none' 
# on ALL pages. Then when you go back to Chat via showPage(), it adds .active 
# but the inline display:none WINS over CSS .page.active{display:flex}
# 
# Solution: Rewrite showPage to clear inline display styles, and rewrite
# the open functions to use .active class only (not inline display)
# ============================================================

# Fix showPage to clear inline display:none when showing a page
old_showPage = """function showPage(p){
  const pg=document.getElementById('page-'+p);
  if(!pg){showToast('Page coming soon!');closeNav();return;}
  document.querySelectorAll('.page').forEach(el=>el.classList.remove('active'));
  pg.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(el=>el.classList.remove('active'));
  const ni=document.getElementById('nav-'+p);if(ni)ni.classList.add('active');
  closeNav();
  if(p==='map')setTimeout(initMap,250);
  if(p==='tracking')setTimeout(initTrackMap,250);
}"""

new_showPage = """function showPage(p){
  const pg=document.getElementById('page-'+p);
  if(!pg){showToast('Page coming soon!');closeNav();return;}
  // Clear ALL inline display styles and active classes
  document.querySelectorAll('.page').forEach(el=>{el.classList.remove('active');el.style.display='';});
  pg.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(el=>el.classList.remove('active'));
  const ni=document.getElementById('nav-'+p);if(ni)ni.classList.add('active');
  closeNav();
  if(p==='map')setTimeout(initMap,250);
  if(p==='tracking')setTimeout(initTrackMap,250);
}"""

if old_showPage in content:
    content = content.replace(old_showPage, new_showPage)
    print("✅ Fixed showPage to clear inline display styles")
else:
    print("❌ Could not find showPage")

# Fix openStorePage — use .active class, clear inline display on return
old_openStore = """function openStorePage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('storePage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-store');
  if (nav) nav.classList.add('active');
  closeNav();
  renderSlimeStore();
}"""

new_openStore = """function openStorePage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('storePage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-store');
  if (nav) nav.classList.add('active');
  closeNav();
  renderSlimeStore();
}
// Override showPage to handle returning from store/stew/slimecode pages
const _origShowPage = showPage;
function showPage(p) {
  // Clear inline display on ALL pages so .active CSS works
  document.querySelectorAll('.page').forEach(el => { el.style.display = ''; });
  _origShowPage(p);
}"""

# Actually, this _origShowPage pattern is EXACTLY what caused the infinite recursion bug before!
# Don't use it. Instead, just fix the showPage function directly (already done above).
# Let's use a simpler approach.

new_openStore = """function openStorePage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('storePage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-store');
  if (nav) nav.classList.add('active');
  closeNav();
  renderSlimeStore();
}"""

if old_openStore in content:
    content = content.replace(old_openStore, new_openStore)
    print("✅ openStorePage kept (showPage fix handles the return)")
else:
    print("❌ Could not find openStorePage")

# Fix openStewPage — add loading state and fallback so it doesn't freeze
old_openStew = """function openStewPage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('stewPage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-stew');
  if (nav) nav.classList.add('active');
  closeNav();
}"""

new_openStew = """function openStewPage() {
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('stewPage');
  if (p) { p.style.display = 'block'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-stew');
  if (nav) nav.classList.add('active');
  closeNav();
  // Show info message if STEW backend is offline
  const output = document.getElementById('stewOutput');
  if (output && !output.textContent.trim()) {
    output.innerHTML = '<div style="opacity:.7;line-height:1.8">🤖 STEW Agent is ready!\\n\\nGive me any task above and I will deploy AI agents to research, analyze, and complete it.\\n\\nExamples:\\n• Research best universities in Canada for Nigerian students\\n• Write a business plan for a fintech startup\\n• Compare phone prices in Nigeria\\n• Find remote jobs for Nigerian developers</div>';
  }
}"""

if old_openStew in content:
    content = content.replace(old_openStew, new_openStew)
    print("✅ Fixed openStewPage with helpful welcome message")
else:
    print("❌ Could not find openStewPage")

# Fix openSlimeCode — add missing closeNav() call
old_openSC = """function openSlimeCode() {
  if (!checkFreeLimit('slimecode')) return;
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('slimecodePage');
  if (p) { p.style.display = 'flex'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-slimecode');
  if (nav) nav.classList.add('active');
}"""

new_openSC = """function openSlimeCode() {
  if (!checkFreeLimit('slimecode')) return;
  document.querySelectorAll('.page').forEach(p => { p.classList.remove('active'); p.style.display = 'none'; });
  const p = document.getElementById('slimecodePage');
  if (p) { p.style.display = 'flex'; p.classList.add('active'); }
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const nav = document.getElementById('nav-slimecode');
  if (nav) nav.classList.add('active');
  closeNav();
}"""

if old_openSC in content:
    content = content.replace(old_openSC, new_openSC)
    print("✅ Fixed openSlimeCode — added missing closeNav()")
else:
    print("❌ Could not find openSlimeCode")

# Remove the dead showPage_sc function
old_showPageSc = """function showPage_sc(id) {
  if (id === 'slimecode') {
    if (!checkFreeLimit('slimecode')) return;
    document.querySelectorAll('.page').forEach(p=>p.style.display='none');
    const p = document.getElementById('slimecodePage');
    if (p) p.style.display='flex';
    const nav = document.getElementById('nav-slimecode');
    document.querySelectorAll('.na').forEach(n=>n.classList.remove('active'));
    if (nav) nav.classList.add('active');
    return;
  }
}

async function scGenerate() {"""

new_showPageSc = """async function scGenerate() {"""

if old_showPageSc in content:
    content = content.replace(old_showPageSc, new_showPageSc)
    print("✅ Removed dead showPage_sc function")
else:
    print("❌ Could not find showPage_sc")

# Fix handleStewTask — make it work even when STEW backend is offline
# by falling back to the main AI (Mistral)
old_handleStew = """function handleStewTask() {
  const input = document.getElementById('stewInput');
  const output = document.getElementById('stewOutput');
  if (!input || !input.value.trim()) { showToast('Please enter a task for STEW'); return; }
  const task = input.value.trim();
  input.value = '';
  if (output) output.innerHTML = '<div style="opacity:.5">🤖 STEW agents deploying... please wait</div>';
  runStewTask(task).then(result => {
    if (output) output.textContent = result;
  }).catch(e => {
    if (output) output.textContent = 'Error: ' + (e.message || 'STEW task failed');
    HEALER.log(e);
  });
}"""

new_handleStew = """function handleStewTask() {
  const input = document.getElementById('stewInput');
  const output = document.getElementById('stewOutput');
  if (!input || !input.value.trim()) { showToast('Please enter a task for STEW'); return; }
  if (!spendCoins('stew_task')) return;
  const task = input.value.trim();
  input.value = '';
  if (output) output.innerHTML = '<div style="opacity:.5">🤖 STEW agents deploying... analyzing your task...</div>';
  runStewTask(task).then(result => {
    if (output) output.textContent = result;
  }).catch(e => {
    if (output) output.textContent = 'Error: ' + (e.message || 'STEW task failed');
    HEALER.log(e);
  });
}"""

if old_handleStew in content:
    content = content.replace(old_handleStew, new_handleStew)
    print("✅ Fixed handleStewTask — added coin check")
else:
    print("❌ Could not find handleStewTask")

# Fix runStewTask — add Mistral AI fallback when STEW backend is offline
old_runStew = """async function runStewTask(task) {
  if (!spendCoins('stew_task')) return;
  setStatus('STEW THINKING');
  const a = addAct('STEW Agent: ' + task.slice(0,50));
  try {
    const res = await fetch(STEW_URL + '/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: task }),
      signal: AbortSignal.timeout(30000)
    });
    if (res.ok) {
      const d = await res.json();
      doneAct(a);
      return d.output || d.response || JSON.stringify(d);
    }
    throw new Error('STEW returned ' + res.status);
  } catch(e) {
    errAct(a);
    return 'STEW Agent is currently offline. Task: ' + task;
  } finally { setStatus('READY'); }
}"""

new_runStew = """async function runStewTask(task) {
  setStatus('STEW THINKING');
  const a = addAct('STEW Agent: ' + task.slice(0,50));
  // First try the STEW backend
  try {
    const res = await fetch(STEW_URL + '/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: task }),
      signal: AbortSignal.timeout(15000)
    });
    if (res.ok) {
      const d = await res.json();
      doneAct(a);
      return d.output || d.response || JSON.stringify(d);
    }
    throw new Error('STEW backend returned ' + res.status);
  } catch(e) {
    // Fallback: use Mistral AI directly as STEW
    try {
      const stewPrompt = 'You are STEW, a multi-agent AI system. You have 100 specialized AI agents at your disposal. A user gives you a task and you coordinate agents to research, analyze, and complete it.\\n\\nTask: ' + task + '\\n\\nProvide a comprehensive, well-structured response. Use headers, bullet points, and actionable advice. If it is research, provide key findings. If it is creative, provide the full output. Be thorough but concise.';
      const res2 = await fetch('https://api.mistral.ai/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + MISTRAL_KEY },
        body: JSON.stringify({
          model: 'mistral-large-latest',
          messages: [{ role: 'system', content: stewPrompt }, { role: 'user', content: task }],
          max_tokens: 4096, temperature: 0.7
        }),
        signal: AbortSignal.timeout(45000)
      });
      if (res2.ok) {
        const d2 = await res2.json();
        doneAct(a);
        return d2.choices?.[0]?.message?.content || 'STEW completed but returned no output.';
      }
      throw new Error('AI fallback also failed');
    } catch(e2) {
      errAct(a);
      HEALER.log(e2);
      return 'STEW Agent is currently having connectivity issues. Please try again in a moment.\\n\\nYour task was: ' + task;
    }
  } finally { setStatus('READY'); }
}"""

if old_runStew in content:
    content = content.replace(old_runStew, new_runStew)
    print("✅ Fixed runStewTask — added Mistral AI fallback when STEW backend is offline")
else:
    print("❌ Could not find runStewTask")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

print(f"\nFile size: {len(content)} chars")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'r') as f:
    content = f.read()

# The stew/shazam/youtube functions were accidentally removed. Let me add them back.
# I'll add them before the "const FREE_LIMITS" line

stew_block = """
// ── STEW AGENT ──
async function runStewTask(task) {
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
}

async function stewSearch(query) {
  if (!spendCoins('stew_research')) return '';
  try {
    const res = await fetch(STEW_URL + '/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query, num_results: 5 }),
      signal: AbortSignal.timeout(15000)
    });
    if (res.ok) {
      const d = await res.json();
      return (d.results || []).slice(0,3).map(r => r.title + ': ' + (r.body||r.snippet||'')).join('\\n');
    }
  } catch(e) { HEALER.log(e); }
  return '';
}

function handleStewTask() {
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
}

// ── SHAZAM MUSIC ID ──
let shazamStream = null;
async function startShazam() {
  if (!spendCoins('shazam')) return;
  showToast('🎵 Listening for music...');
  setStatus('IDENTIFYING MUSIC');
  const a = addAct('Shazam: Listening...');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    shazamStream = stream;
    const mediaRecorder = new MediaRecorder(stream);
    const chunks = [];
    mediaRecorder.ondataavailable = e => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunks, { type: 'audio/webm' });
      const formData = new FormData();
      formData.append('file', blob, 'audio.webm');
      formData.append('return', 'spotify,apple_music,deezer');
      formData.append('api_token', K.acrcloud || '');
      try {
        const res = await fetch('https://identify-eu-west-1.acrcloud.com/v1/identify', {
          method: 'POST', body: formData, signal: AbortSignal.timeout(15000)
        });
        if (res.ok) {
          const d = await res.json();
          if (d.status && d.status.code === 0 && d.metadata && d.metadata.music) {
            const song = d.metadata.music[0];
            const result = song.title + ' by ' + (song.artists||[{name:'Unknown'}])[0].name + ' (' + (song.album||{name:''}).name + ')';
            doneAct(a);
            addMsg('ai', '🎵 Song identified: ' + result);
            setStatus('READY');
            return;
          }
        }
      } catch(e) { HEALER.log(e); }
      errAct(a);
      addMsg('ai', 'Could not identify the song. Make sure music is playing clearly.');
      setStatus('READY');
    };
    mediaRecorder.start();
    setTimeout(() => { if (mediaRecorder.state !== 'inactive') mediaRecorder.stop(); }, 8000);
    showToast('Recording 8 seconds of audio...');
  } catch(e) {
    errAct(a);
    showToast('Microphone access needed for Shazam');
    setStatus('READY');
    HEALER.log(e);
  }
}

// ── YOUTUBE VIDEO READER ──
async function readYouTubeVideo(url) {
  if (!spendCoins('youtube_reader')) return;
  setStatus('READING VIDEO');
  const a = addAct('Reading YouTube video...');
  const videoId = url.match(/(?:v=|youtu\\.be\\/)([a-zA-Z0-9_-]{11})/)?.[1];
  if (!videoId) {
    errAct(a);
    addMsg('ai', 'Invalid YouTube URL. Please paste a valid YouTube link.');
    setStatus('READY');
    return;
  }
  try {
    const res = await fetch(`https://yt-transcript-proxy.vercel.app/api/transcript?videoId=${videoId}`, {
      signal: AbortSignal.timeout(20000)
    });
    if (res.ok) {
      const d = await res.json();
      const transcript = (d.transcript || []).map(t => t.text).join(' ').slice(0, 6000);
      doneAct(a);
      chatHistory.push({
        role: 'user',
        content: `[YouTube Video Transcript - ID: ${videoId}]\\n${transcript}\\n\\nPlease summarize this video and highlight the key points.`
      });
      await callAI('Summarize this YouTube video transcript and give me the key points.');
    } else {
      throw new Error('Transcript not available');
    }
  } catch(e) {
    errAct(a);
    addMsg('ai', 'Could not read this video transcript. The video may have no captions or is private. Try a different video.');
    setStatus('READY');
    HEALER.log(e);
  }
}

function checkYouTubeLink(text) {
  const ytMatch = text.match(/(https?:\\/\\/)?(www\\.)?(youtube\\.com\\/watch\\?v=|youtu\\.be\\/)[a-zA-Z0-9_-]{11}/);
  if (ytMatch) {
    const url = ytMatch[0].startsWith('http') ? ytMatch[0] : 'https://' + ytMatch[0];
    if (confirm('YouTube video detected! Read and summarize this video?')) {
      readYouTubeVideo(url);
      return true;
    }
  }
  return false;
}

"""

# Add the block before FREE_LIMITS
insert_point = "const FREE_LIMITS = { chat:15"
if insert_point in content:
    content = content.replace(insert_point, stew_block + insert_point)
    print("✅ FIX 10: Restored runStewTask, handleStewTask, startShazam, readYouTubeVideo, checkYouTubeLink")
else:
    print("❌ FIX 10: Could not find FREE_LIMITS insertion point")

with open('/app/incoming_files/6a37c020c4136b72832135f0/SlimeAI_v26_FIXED.html', 'w') as f:
    f.write(content)

print(f"File size: {len(content)} chars")

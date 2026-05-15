  const audio = document.getElementById('audio');
  let currentId = null;
  let episodes = [];

  // ── Auth ──────────────────────────────────────────
  let _googleToken = null;

  function authHeaders() {
    return _googleToken ? { 'Authorization': `Bearer ${_googleToken}` } : {};
  }

  async function authFetch(url, opts = {}) {
    const headers = { ...authHeaders(), ...(opts.headers || {}) };
    const res = await fetch(url, { ...opts, headers });
    if (res.status === 401) {
      _googleToken = null;
      showSignIn();
      throw new Error('Unauthorized');
    }
    return res;
  }

  function showSignIn() {
    document.getElementById('signInScreen').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
    document.getElementById('signOutBtn').style.display = 'none';
  }

  function showApp(email) {
    document.getElementById('signInScreen').style.display = 'none';
    document.getElementById('app').style.display = '';
    if (email) {
      const btn = document.getElementById('signOutBtn');
      btn.title = `Sign out (${email})`;
      btn.setAttribute('aria-label', `Sign out (${email})`);
      btn.style.display = '';
    }
    loadStatus();
  }

  document.getElementById('signOutBtn').addEventListener('click', () => {
    _googleToken = null;
    localStorage.removeItem('gToken');
    showSignIn();
    if (typeof google !== 'undefined') {
      google.accounts.id.disableAutoSelect();
      google.accounts.id.renderButton(
        document.getElementById('gisButton'),
        { theme: 'filled_black', size: 'large', text: 'signin_with' }
      );
      google.accounts.id.prompt();
    }
  });

  function _decodeJwtPayload(token) {
    return JSON.parse(atob(token.split('.')[1]));
  }

  async function initAuth() {
    const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
    if (!cfg.google_client_id) {
      showApp();
      loadEpisodes();
      return;
    }
    // Restore cached token if still valid (exp > now + 60s)
    const cached = localStorage.getItem('gToken');
    if (cached) {
      try {
        const payload = _decodeJwtPayload(cached);
        if (payload.exp * 1000 > Date.now() + 60000) {
          _googleToken = cached;
          showApp(payload.email);
          loadEpisodes();
          return;
        }
      } catch {}
      localStorage.removeItem('gToken');
    }
    // Multi-user: hide app until signed in
    showSignIn();
    // Load GIS dynamically
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://accounts.google.com/gsi/client';
      s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
    window._handleGoogleToken = (response) => {
      _googleToken = response.credential;
      localStorage.setItem('gToken', _googleToken);
      try {
        const payload = _decodeJwtPayload(_googleToken);
        showApp(payload.email);
      } catch { showApp(); }
      loadEpisodes();
    };
    // Render GIS button
    google.accounts.id.initialize({
      client_id: cfg.google_client_id,
      callback: window._handleGoogleToken,
      auto_select: true,
    });
    google.accounts.id.renderButton(
      document.getElementById('gisButton'),
      { theme: 'filled_black', size: 'large', text: 'signin_with' }
    );
    google.accounts.id.prompt();
  }

  // ── Helpers ──────────────────────────────────────
  function fmt(s) {
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }
  function relTime(iso) {
    if (!iso) return '—';
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }
  function fmtCountdown(s) {
    if (s <= 0) return 'soon';
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  }
  function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  // ── Cover (default SVG, replaced by uploaded image if available) ──
  const coverEl = document.getElementById('cover');
  const playerCoverEl = document.getElementById('playerCover');
  function tryLoadCover() {
    for (const ext of ['jpg', 'jpeg', 'png']) {
      const img = new Image();
      img.src = `/cover.${ext}`;
      img.onload = () => {
        coverEl.innerHTML = '';
        coverEl.appendChild(img);
        const img2 = new Image();
        img2.src = img.src;
        playerCoverEl.innerHTML = '';
        playerCoverEl.appendChild(img2);
      };
    }
  }
  tryLoadCover();

  // ── Feed color palette ────────────────────────────
  const FEED_COLORS = ['#7c4dff','#0ea5e9','#10b981','#f59e0b','#ef4444','#ec4899','#8b5cf6','#06b6d4'];
  const _feedColorMap = {};
  function feedColor(feedName) {
    if (!_feedColorMap[feedName]) {
      const idx = Object.keys(_feedColorMap).length % FEED_COLORS.length;
      _feedColorMap[feedName] = FEED_COLORS[idx];
    }
    return _feedColorMap[feedName];
  }

  function episodeRow(ep, showFeedBadge = false) {
    const badge = showFeedBadge
      ? `<span class="ep-feed-badge" style="background:${feedColor(ep.feed_name || 'imported')}">${escapeHtml(ep.notebook || ep.feed_name || '')}</span>`
      : '';
    return `
      <div class="ep-wrap">
        <div class="episode" id="ep-${escapeHtml(ep.id)}" data-id="${escapeHtml(ep.id)}" data-url="${escapeHtml(ep.url)}">
          <input type="checkbox" class="ep-check" data-check-id="${escapeHtml(ep.id)}" aria-label="Select episode">
          <button class="play-btn" aria-label="Play ${escapeHtml(ep.title)}">
            <svg class="icon-play" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            <svg class="icon-pause" viewBox="0 0 24 24" fill="currentColor" style="display:none"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>
          </button>
          <div class="ep-info">
            <div class="ep-title">${escapeHtml(ep.title)}</div>
            <div class="ep-meta">${relTime(ep.created_at)}</div>
          </div>
          <div class="ep-aside">
            <span class="eq" aria-hidden="true"><span></span><span></span><span></span></span>
            ${badge}
            ${ep.duration ? `<span class="ep-duration">${fmt(ep.duration)}</span>` : ''}
          </div>
        </div>
        <button class="swipe-del" data-del-id="${escapeHtml(ep.id)}" aria-label="Delete episode">Delete</button>
      </div>`;
  }

  // ── Episodes ─────────────────────────────────────
  let _lastEpisodesKey = null;

  async function loadEpisodes() {
    const container = document.getElementById('episodes');
    try {
      const res = await authFetch('/api/episodes');
      const fresh = await res.json();
      const key = JSON.stringify(fresh.map(e => e.id + e.created_at));
      if (key === _lastEpisodesKey) return;
      _lastEpisodesKey = key;
      episodes = fresh;

      const feedCount = new Set(fresh.map(e => e.feed_name)).size;
      const epText = episodes.length === 1 ? '1 episode' : `${episodes.length} episodes`;
      const subtitle = feedCount > 1 ? `${epText} · ${feedCount} feeds` : epText;
      document.getElementById('subtitle').textContent = subtitle;
      document.getElementById('sectionCount').textContent = epText;

      if (!episodes.length) {
        container.innerHTML = `
          <div class="empty">
            <div class="empty-illu">
              <svg viewBox="0 0 64 64" fill="none">
                <g fill="white" opacity="0.95">
                  <rect x="6"  y="26" width="6" height="12" rx="3"/>
                  <rect x="16" y="20" width="6" height="24" rx="3"/>
                  <rect x="26" y="12" width="6" height="40" rx="3"/>
                  <rect x="36" y="18" width="6" height="28" rx="3"/>
                  <rect x="46" y="24" width="6" height="16" rx="3"/>
                </g>
              </svg>
            </div>
            <h3>No episodes yet</h3>
            <p>NoteCast polls your RSS feeds and generates AI podcasts via NotebookLM. Episodes appear here once ready.</p>
          </div>`;
        return;
      }

      // Group by feed_name (preserving order of first appearance)
      const feedOrder = [];
      const byFeed = {};
      for (const ep of episodes) {
        const fn = ep.feed_name || 'imported';
        if (!byFeed[fn]) { byFeed[fn] = []; feedOrder.push(fn); }
        byFeed[fn].push(ep);
      }

      if (feedOrder.length === 1) {
        // Single feed — flat list, no group header, no badge needed
        container.className = 'episodes';
        container.innerHTML = episodes.map(ep => episodeRow(ep, false)).join('');
      } else {
        // Multiple feeds — grouped with headers, badge on each row
        container.className = '';
        container.innerHTML = feedOrder.map(fn => {
          const eps = byFeed[fn];
          const label = eps[0].notebook || fn;
          const color = feedColor(fn);
          return `
            <div class="feed-group">
              <div class="feed-group-header">
                <span class="feed-dot" style="background:${color}"></span>
                <span class="feed-group-title">${escapeHtml(label)}</span>
                <span class="feed-group-count">${eps.length}</span>
              </div>
              <div class="episodes">${eps.map(ep => episodeRow(ep, false)).join('')}</div>
            </div>`;
        }).join('');
      }

      container.querySelectorAll('.episode').forEach(el => {
        el.addEventListener('click', e => {
          if (selectMode) return;
          const tx = new DOMMatrix(getComputedStyle(el).transform).m41;
          if (tx < -10) { resetSwipe(el); return; }
          playEpisode(el.dataset.id, el.dataset.url, el.querySelector('.ep-title').textContent);
        });
      });

      selectedIds.clear();
      updateBulkBar();

      container.querySelectorAll('.ep-check').forEach(cb => {
        cb.addEventListener('change', e => {
          e.stopPropagation();
          toggleSelect(cb.dataset.checkId, cb.checked);
        });
        cb.addEventListener('click', e => e.stopPropagation());
      });

      const isTouch = window.matchMedia('(hover: none), (pointer: coarse)').matches;
      if (isTouch) container.querySelectorAll('.ep-wrap').forEach(wrap => attachSwipe(wrap));

      container.querySelectorAll('.swipe-del').forEach(btn => {
        btn.addEventListener('click', async e => {
          e.stopPropagation();
          const id = btn.dataset.delId;
          const wrap = btn.closest('.ep-wrap');
          const row = document.getElementById(`ep-${id}`);
          const title = row?.querySelector('.ep-title')?.textContent || 'this episode';
          if (!confirm(`Delete "${title}"?\nThis removes the audio file and cannot be undone.`)) {
            resetSwipe(row); return;
          }
          btn.disabled = true;
          try {
            const res = await authFetch(`/api/episodes/${id}`, { method: 'DELETE' });
            if (res.ok) {
              wrap?.remove();
              episodes = episodes.filter(e => e.id !== id);
              const count = episodes.length;
              document.getElementById('sectionCount').textContent = count === 1 ? '1 episode' : `${count} episodes`;
            } else {
              const j = await res.json().catch(() => ({}));
              alert(j.error || 'Delete failed');
              btn.disabled = false; resetSwipe(row);
            }
          } catch {
            alert('Delete failed');
            btn.disabled = false; resetSwipe(row);
          }
        });
      });
    } catch {
      container.innerHTML = '<div class="empty"><p>Could not load episodes.</p></div>';
    }
  }

  // ── Player ───────────────────────────────────────
  function playEpisode(id, url, title) {
    document.querySelectorAll('.episode').forEach(e => e.classList.remove('playing'));
    const el = document.getElementById(`ep-${id}`);
    if (el) el.classList.add('playing');

    if (currentId === id && !audio.paused) {
      audio.pause();
      setPlayIcon(false);
      setEpisodePlayIcon(id, false);
      return;
    }

    if (currentId && currentId !== id) setEpisodePlayIcon(currentId, false);
    currentId = id;
    if (audio.src !== url) {
      audio.src = url;
      audio.load();
    }
    audio.play();
    document.getElementById('playerTitle').textContent = title;
    const ep = episodes.find(e => e.id === id);
    document.getElementById('playerSub').textContent = ep?.notebook ? ep.notebook : 'Now playing';
    document.getElementById('player').classList.add('visible');
    setPlayIcon(true);
    setEpisodePlayIcon(id, true);
  }

  function setPlayIcon(playing) {
    document.getElementById('iconPlay').style.display = playing ? 'none' : '';
    document.getElementById('iconPause').style.display = playing ? '' : 'none';
  }

  function setEpisodePlayIcon(id, playing) {
    const el = document.getElementById(`ep-${id}`);
    if (!el) return;
    el.querySelector('.icon-play').style.display = playing ? 'none' : '';
    el.querySelector('.icon-pause').style.display = playing ? '' : 'none';
  }

  document.getElementById('playPause').addEventListener('click', () => {
    if (audio.paused) { audio.play(); setPlayIcon(true); setEpisodePlayIcon(currentId, true); }
    else { audio.pause(); setPlayIcon(false); setEpisodePlayIcon(currentId, false); }
  });
  document.getElementById('skipBack').addEventListener('click', () => audio.currentTime = Math.max(0, audio.currentTime - 15));
  document.getElementById('skipFwd').addEventListener('click', () => audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 30));

  audio.addEventListener('timeupdate', () => {
    const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('timeElapsed').textContent = fmt(audio.currentTime);
    document.getElementById('timeDuration').textContent = fmt(audio.duration);
  });
  audio.addEventListener('ended', () => {
    setPlayIcon(false);
    setEpisodePlayIcon(currentId, false);
    document.querySelectorAll('.episode').forEach(e => e.classList.remove('playing'));
    const idx = episodes.findIndex(e => e.id === currentId);
    if (idx >= 0 && idx < episodes.length - 1) {
      const next = episodes[idx + 1];
      playEpisode(next.id, next.url, next.title);
    }
  });

  document.getElementById('progressBar').addEventListener('click', e => {
    if (!audio.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
  });

  // ── Admin ─────────────────────────────────────────
  const overlay = document.getElementById('overlay');
  function openAdmin() {
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    loadStatus();
    loadFeedCfg();
  }
  function closeAdmin() {
    overlay.classList.remove('open');
    document.body.style.overflow = '';
  }
  document.getElementById('adminBtn').addEventListener('click', openAdmin);
  document.getElementById('closeAdmin').addEventListener('click', closeAdmin);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeAdmin(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAdmin(); });

  document.getElementById('queueStatusToggle').addEventListener('click', () => {
    const list = document.getElementById('queueJobList');
    const chevron = document.getElementById('queueChevron');
    const open = list.style.display === 'block';
    list.style.display = open ? 'none' : 'block';
    chevron.classList.toggle('open', !open);
  });

  function makeAccordion(headerId, bodyId, chevId) {
    document.getElementById(headerId).addEventListener('click', () => {
      const body = document.getElementById(bodyId);
      const chev = document.getElementById(chevId);
      const open = body.style.display !== 'none';
      body.style.display = open ? 'none' : 'block';
      chev.classList.toggle('open', !open);
    });
  }
  makeAccordion('feedsAccordion', 'feedsBody', 'feedsChev');
  makeAccordion('sessionAccordion', 'sessionBody', 'sessionChev');

  document.getElementById('renewCredsToggle').addEventListener('click', () => {
    const body = document.getElementById('renewCredsBody');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    document.getElementById('renewCredsToggle').textContent = open ? 'Renew credentials' : 'Cancel';
  });

  document.getElementById('webhookTestBtn').addEventListener('click', async () => {
    const btn = document.getElementById('webhookTestBtn');
    btn.textContent = 'Sending…'; btn.disabled = true;
    try {
      const r = await authFetch('/api/webhook/test', { method: 'POST' });
      btn.textContent = r.ok ? 'Sent!' : 'Error';
    } catch { btn.textContent = 'Error'; }
    setTimeout(() => { btn.textContent = 'Test'; btn.disabled = false; }, 2000);
  });

  document.getElementById('pollNowBtn').addEventListener('click', async () => {
    const btn = document.getElementById('pollNowBtn');
    btn.textContent = 'Checking…';
    btn.disabled = true;
    try {
      const r = await authFetch('/api/poll', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      const n = d.queued ?? 0;
      btn.textContent = n > 0 ? `Queued ${n} new!` : 'Up to date';
      if (n > 0) loadStatus();
    } catch {
      btn.textContent = 'Error';
    }
    setTimeout(() => { btn.textContent = 'Check now'; btn.disabled = false; }, 2500);
  });

  async function loadStatus() {
    try {
      const s = await authFetch('/api/status').then(r => r.json());
      document.getElementById('statEpisodes').textContent = s.episodes ?? '—';

      const pending = s.pending ?? 0;
      const generating = s.generating ?? 0;
      const queueEl = document.getElementById('statQueue');
      const queueRow = document.getElementById('queueStatusRow');
      const queueText = document.getElementById('queueStatusText');
      const queueJobList = document.getElementById('queueJobList');
      if (pending + generating === 0) {
        queueEl.textContent = '—';
        queueEl.style.color = 'var(--text-2)';
        queueRow.style.display = 'none';
      } else {
        const parts = [];
        if (generating > 0) parts.push(`${generating} generating`);
        if (pending > 0) parts.push(`${pending} pending`);
        queueEl.textContent = pending + generating;
        queueEl.style.color = 'var(--accent)';
        queueRow.style.display = 'block';
        queueText.textContent = parts.join(' · ');
        const jobs = s.queue_jobs ?? [];
        queueJobList.innerHTML = jobs.map(j => {
          const ts = j.status === 'generating' ? j.updated_at : j.created_at;
          const label = j.status === 'generating' ? 'started' : 'queued';
          return `<div class="queue-job-row">
            <span class="queue-job-badge ${j.status}">${j.status}</span>
            <span class="queue-job-title" title="${j.title}">${j.title}</span>
            <span class="queue-job-meta">${j.feed_name} · ${label} ${relTime(ts)}</span>
          </div>`;
        }).join('');
      }
      document.getElementById('statUpdated').textContent = s.last_updated ? relTime(s.last_updated) : '—';
      if (s.version) document.getElementById('appVersion').textContent = `NoteCast ${s.version}`;
      if (s.feed_url) feedUrl = s.feed_url;
      if (s.feed_token) {
        document.getElementById('scriptTokenValue').textContent = s.feed_token;
        document.getElementById('scriptTokenRow').style.display = 'flex';
      }
      const webhookRow = document.getElementById('webhookRow');
      webhookRow.style.display = s.webhook_enabled ? 'flex' : 'none';

      // Update token expiry with warning colors + session badge
      const tokenElem = document.getElementById('statTokenExpiry');
      const badge = document.getElementById('sessionBadge');
      if (s.token_expires_in_days !== undefined) {
        const days = s.token_expires_in_days;
        if (days < 0) {
          tokenElem.textContent = 'Expired ⚠️';
          tokenElem.style.color = '#dc2626';
          badge.textContent = '⚠️ Expired — upload new credentials';
          badge.style.color = '#dc2626';
        } else if (days === 0) {
          tokenElem.textContent = 'Expires today ⚠️';
          tokenElem.style.color = '#dc2626';
          badge.textContent = '⚠️ Expires today';
          badge.style.color = '#dc2626';
        } else if (days === 1) {
          tokenElem.textContent = 'Tomorrow ⚠️';
          tokenElem.style.color = '#dc2626';
          badge.textContent = '⚠️ Expires tomorrow';
          badge.style.color = '#dc2626';
        } else if (days < 7) {
          tokenElem.textContent = `${days} days ⚠️`;
          tokenElem.style.color = '#ea580c';
          badge.textContent = `⚠️ Expires in ${days} days`;
          badge.style.color = '#ea580c';
        } else {
          tokenElem.textContent = `${days} days`;
          tokenElem.style.color = 'var(--text)';
          badge.textContent = `✓ Valid · ${days} days`;
          badge.style.color = '#16a34a';
        }
      } else {
        tokenElem.textContent = '—';
        tokenElem.style.color = 'var(--text)';
        badge.textContent = 'No credentials';
        badge.style.color = 'var(--text-3)';
      }
    } catch {}
  }

  let feedUrl = `${location.origin}/feed.xml`;

  document.getElementById('copyFeedBtn').addEventListener('click', () => {
    if (feedUrl) {
      // Single feed — copy directly
      navigator.clipboard.writeText(feedUrl).then(() => {
        const wrap = document.getElementById('copyFeedWrap');
        wrap.classList.add('copied');
        setTimeout(() => wrap.classList.remove('copied'), 1500);
      });
    } else {
      // Multi feed — toggle dropdown
      document.getElementById('feedDropdown').classList.toggle('open');
    }
  });

  // Close dropdown when clicking outside
  document.addEventListener('click', (e) => {
    if (!document.getElementById('copyFeedWrap').contains(e.target)) {
      document.getElementById('feedDropdown').classList.remove('open');
    }
  });

  function copyFeedUrl(btn, url) {
    navigator.clipboard.writeText(url).then(() => {
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    });
  }

  const authFile = document.getElementById('authFile');
  authFile.addEventListener('change', () => {
    document.getElementById('authFilename').textContent = authFile.files[0]?.name || '';
  });

  const uploadArea = document.getElementById('uploadArea');
  uploadArea.addEventListener('dragover', e => { e.preventDefault(); uploadArea.style.borderColor = 'var(--accent)'; uploadArea.style.background = 'var(--accent-softer)'; });
  uploadArea.addEventListener('dragleave', () => { uploadArea.style.borderColor = ''; uploadArea.style.background = ''; });
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.style.borderColor = ''; uploadArea.style.background = '';
    const file = e.dataTransfer.files[0];
    if (!file) return;
    const dt = new DataTransfer(); dt.items.add(file);
    authFile.files = dt.files;
    document.getElementById('authFilename').textContent = file.name;
  });

  document.getElementById('uploadBtn').addEventListener('click', async () => {
    const file = authFile.files[0];
    const result = document.getElementById('uploadResult');
    if (!file) {
      result.textContent = 'Choose a file first.';
      result.className = 'upload-result err';
      result.style.display = 'block';
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await authFetch('/api/auth/upload', { method: 'POST', body: fd });
      const json = await res.json();
      result.textContent = json.ok ? '✓ Credentials updated' : json.error;
      result.className = 'upload-result ' + (json.ok ? 'ok' : 'err');
    } catch {
      result.textContent = 'Upload failed.';
      result.className = 'upload-result err';
    }
    result.style.display = 'block';
  });

  // ── Select mode + swipe-to-delete ───────────────
  const selectedIds = new Set();
  let selectMode = false;

  function enterSelectMode() {
    selectMode = true;
    document.getElementById('episodes').classList.add('select-mode');
    document.getElementById('selectModeBtn').textContent = 'Done';
  }

  function exitSelectMode() {
    selectMode = false;
    document.getElementById('episodes').classList.remove('select-mode');
    document.getElementById('selectModeBtn').textContent = 'Select';
    document.querySelectorAll('.ep-check').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.episode.selected').forEach(el => el.classList.remove('selected'));
    selectedIds.clear();
    updateBulkBar();
  }

  document.getElementById('selectModeBtn').addEventListener('click', () => {
    selectMode ? exitSelectMode() : enterSelectMode();
  });

  function resetSwipe(epEl) {
    if (!epEl) return;
    epEl.style.transition = 'transform .22s ease';
    epEl.style.transform = 'translateX(0)';
    epEl.closest('.ep-wrap')?.classList.remove('swiped');
  }

  function attachSwipe(wrap) {
    const ep = wrap.querySelector('.episode');
    const DEL_W = 80, SNAP = 50;
    let startX, startY, tracking = false, swiped = false, moved = false;

    ep.addEventListener('pointerdown', e => {
      if (selectMode || e.button !== 0) return;
      startX = e.clientX; startY = e.clientY;
      tracking = true; moved = false;
      ep.style.transition = 'none';
      ep.setPointerCapture(e.pointerId);
    });

    ep.addEventListener('pointermove', e => {
      if (!tracking) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (!moved && Math.abs(dy) > Math.abs(dx)) { tracking = false; ep.style.transition = 'transform .22s ease'; return; }
      moved = true;
      const base = swiped ? -DEL_W : 0;
      const tx = Math.max(-DEL_W, Math.min(0, base + dx));
      ep.style.transform = `translateX(${tx}px)`;
    });

    const release = () => {
      if (!tracking) return;
      tracking = false;
      ep.style.transition = 'transform .22s ease';
      const tx = new DOMMatrix(getComputedStyle(ep).transform).m41;
      if (tx < -SNAP) {
        ep.style.transform = `translateX(-${DEL_W}px)`;
        swiped = true; wrap.classList.add('swiped');
      } else {
        ep.style.transform = 'translateX(0)';
        swiped = false; wrap.classList.remove('swiped');
      }
    };
    ep.addEventListener('pointerup', release);
    ep.addEventListener('pointercancel', release);
  }

  // Reset any open swipe when clicking outside
  document.addEventListener('pointerdown', e => {
    if (e.target.closest('.ep-wrap')) return;
    document.querySelectorAll('.ep-wrap.swiped').forEach(wrap => {
      resetSwipe(wrap.querySelector('.episode'));
    });
  });

  function updateBulkBar() {
    const bar = document.getElementById('bulkBar');
    const countEl = document.getElementById('bulkCount');
    const n = selectedIds.size;
    if (n === 0) { bar.classList.remove('visible'); return; }
    bar.classList.add('visible');
    countEl.textContent = `${n} selected`;
  }

  function toggleSelect(id, checked) {
    const row = document.getElementById(`ep-${id}`);
    if (checked) { selectedIds.add(id); row?.classList.add('selected'); }
    else          { selectedIds.delete(id); row?.classList.remove('selected'); }
    updateBulkBar();
  }

  document.getElementById('selectAllBtn').addEventListener('click', () => {
    document.querySelectorAll('.ep-check').forEach(cb => {
      cb.checked = true;
      selectedIds.add(cb.dataset.checkId);
      document.getElementById(`ep-${cb.dataset.checkId}`)?.classList.add('selected');
    });
    updateBulkBar();
  });

  document.getElementById('clearSelBtn').addEventListener('click', () => {
    document.querySelectorAll('.ep-check').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.episode.selected').forEach(el => el.classList.remove('selected'));
    selectedIds.clear();
    updateBulkBar();
  });

  document.getElementById('bulkDeleteBtn').addEventListener('click', async () => {
    if (!selectedIds.size) return;
    const n = selectedIds.size;
    if (!confirm(`Delete ${n} episode${n > 1 ? 's' : ''}?\nAudio files will be removed and cannot be recovered.`)) return;
    const btn = document.getElementById('bulkDeleteBtn');
    btn.textContent = 'Deleting…'; btn.disabled = true;
    const ids = [...selectedIds];
    let failed = 0;
    for (const id of ids) {
      try {
        const res = await authFetch(`/api/episodes/${id}`, { method: 'DELETE' });
        if (res.ok) {
          document.getElementById(`ep-${id}`)?.closest('.ep-wrap')?.remove();
          episodes = episodes.filter(e => e.id !== id);
          selectedIds.delete(id);
        } else { failed++; }
      } catch { failed++; }
    }
    const count = episodes.length;
    document.getElementById('sectionCount').textContent = count === 1 ? '1 episode' : `${count} episodes`;
    btn.textContent = 'Delete selected'; btn.disabled = false;
    updateBulkBar();
    if (failed) alert(`${failed} episode(s) could not be deleted.`);
  });

  document.getElementById('copyTokenBtn').addEventListener('click', () => {
    const token = document.getElementById('scriptTokenValue').textContent;
    if (!token) return;
    navigator.clipboard.writeText(token).then(() => {
      const btn = document.getElementById('copyTokenBtn');
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    });
  });

  document.getElementById('browserCookiesBtn').addEventListener('click', async () => {
    const btn = document.getElementById('browserCookiesBtn');
    const browser = document.getElementById('browserSelect').value;
    const result = document.getElementById('browserCookiesResult');
    btn.textContent = 'Importing…';
    btn.disabled = true;
    try {
      const res = await authFetch('/api/auth/browser-cookies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ browser }),
      });
      const json = await res.json();
      result.textContent = json.ok ? `✓ Imported from ${browser}` : (json.error || 'Import failed');
      result.className = 'upload-result ' + (json.ok ? 'ok' : 'err');
    } catch {
      result.textContent = 'Import failed.';
      result.className = 'upload-result err';
    }
    result.style.display = 'block';
    btn.textContent = 'Import from browser';
    btn.disabled = false;
  });

  // ── Feed Configuration ────────────────────────────
  let _feedCfgData = [];

  function normalizeYouTubeUrl(raw) {
    let url;
    try { url = new URL(raw.trim()); } catch { return { value: raw, hint: null }; }
    const host = url.hostname.replace(/^www\./, '');
    if (host !== 'youtube.com' && host !== 'youtu.be') return { value: raw, hint: null };

    const list = url.searchParams.get('list');
    const channelId = url.pathname.match(/^\/channel\/(UC[\w-]+)/)?.[1];
    const handle = url.pathname.match(/^\/@([\w.-]+)/)?.[1];

    if (list && (list.startsWith('PL') || list.startsWith('FL') || list.startsWith('UU'))) {
      const fixed = `https://www.youtube.com/feeds/videos.xml?playlist_id=${list}`;
      return { value: fixed, hint: 'Converted to RSS playlist feed' };
    }
    if (channelId) {
      const fixed = `https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`;
      return { value: fixed, hint: 'Converted to RSS channel feed' };
    }
    if (handle) {
      return { value: raw, hint: '⚠ @handle URLs need a channel_id — open the channel page, view source, find "channelId"' };
    }
    if (url.pathname.startsWith('/watch')) {
      return { value: raw, hint: '⚠ Single video URL — use a channel or playlist feed URL instead' };
    }
    return { value: raw, hint: null };
  }

  function _applyUrlNormalization(input) {
    const { value, hint } = normalizeYouTubeUrl(input.value);
    if (value !== input.value) input.value = value;
    const card = input.closest('.feed-card');
    let hintEl = card.querySelector('.feed-cfg-url-hint');
    if (hint) {
      if (!hintEl) {
        hintEl = document.createElement('div');
        hintEl.className = 'feed-cfg-url-hint';
        input.parentElement.after(hintEl);
      }
      hintEl.textContent = hint;
      hintEl.classList.toggle('warn', hint.startsWith('⚠'));
    } else if (hintEl) {
      hintEl.remove();
    }
  }

  async function _saveFeedCard(card, idx, publishedMap) {
    const get = f => card.querySelector(`[data-field="${f}"]`)?.value ?? '';
    const updated = {
      name: get('name'),
      url: normalizeYouTubeUrl(get('url')).value,
      title: get('title'),
      style: get('style') || 'deep-dive',
      language: get('language') || 'en',
      max_episodes: (n => Number.isNaN(n) ? 1 : Math.max(1, n))(parseInt(get('max_episodes'), 10)),
      instructions: get('instructions'),
    };
    const result = document.getElementById('feedCfgResult');
    if (!updated.name.trim()) {
      result.textContent = 'Feed needs a name.';
      result.className = 'upload-result err';
      result.style.display = 'block';
      setTimeout(() => { result.style.display = 'none'; }, 4000);
      return;
    }
    if (!updated.url.trim() || !updated.url.startsWith('http')) {
      result.textContent = 'Feed needs a valid URL (must start with http).';
      result.className = 'upload-result err';
      result.style.display = 'block';
      setTimeout(() => { result.style.display = 'none'; }, 4000);
      return;
    }
    _feedCfgData[idx] = updated;
    try {
      const res = await authFetch('/api/transformer-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_feedCfgData),
      });
      const json = await res.json();
      if (json.ok) {
        _renderFeedCfg(publishedMap);
      } else {
        result.textContent = json.error || 'Save failed';
        result.className = 'upload-result err';
        result.style.display = 'block';
      }
    } catch {
      result.textContent = 'Save failed.';
      result.className = 'upload-result err';
      result.style.display = 'block';
    }
  }

  function _makeFeedCard(feed, idx, published, publishedMap) {
    const card = document.createElement('div');
    card.className = 'feed-card';
    card.dataset.idx = idx;
    const rssUrl = published?.url || '';
    const epCount = published?.episode_count ?? 0;
    const dotColor = feedColor(feed.name || 'new');
    const SVG_LOCKED = `<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><rect x="2" y="7" width="12" height="9" rx="2"/><path d="M5 7V5a3 3 0 016 0v2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
    const SVG_UNLOCKED = `<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><rect x="2" y="7" width="12" height="9" rx="2"/><path d="M5 7V5a3 3 0 016 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
    card.innerHTML = `
      <div class="feed-card-hdr">
        <span class="feed-card-lock">${SVG_LOCKED}</span>
        <span class="feed-dot" style="background:${dotColor};flex-shrink:0"></span>
        <span class="feed-card-name">${escapeHtml(feed.name || 'New feed')}</span>
        <span class="feed-card-count">${epCount} ep</span>
        <button class="btn feed-card-copy-btn" data-url="${escapeHtml(rssUrl)}"
                style="padding:4px 9px;font-size:11px;flex-shrink:0;${rssUrl ? '' : 'display:none'}">RSS</button>
        <button class="btn feed-card-edit-btn"
                style="padding:4px 9px;font-size:11px;flex-shrink:0;">Edit</button>
      </div>
      <div class="feed-card-edit-fields">
        <div class="feed-cfg-row"><label>Name *</label>
          <input class="feed-cfg-input" data-field="name" value="${escapeHtml(feed.name || '')}" placeholder="my-feed"></div>
        <div class="feed-cfg-row"><label>URL *</label>
          <input class="feed-cfg-input" data-field="url" value="${escapeHtml(feed.url || '')}" placeholder="https://..."></div>
        <div class="feed-cfg-row"><label>Title</label>
          <input class="feed-cfg-input" data-field="title" value="${escapeHtml(feed.title || '')}" placeholder="Feed title"></div>
        <div class="feed-cfg-row"><label>Style</label>
          <select class="feed-cfg-select" data-field="style">
            <option value="deep-dive"${feed.style === 'deep-dive' ? ' selected' : ''}>Deep dive</option>
            <option value="brief"${feed.style === 'brief' ? ' selected' : ''}>Brief</option>
            <option value="critique"${feed.style === 'critique' ? ' selected' : ''}>Critique</option>
            <option value="debate"${feed.style === 'debate' ? ' selected' : ''}>Debate</option>
          </select></div>
        <div class="feed-cfg-row"><label>Language</label>
          <input class="feed-cfg-input" data-field="language" value="${escapeHtml(feed.language || 'en')}" placeholder="en"></div>
        <div class="feed-cfg-row"><label>Max episodes</label>
          <input class="feed-cfg-input" type="number" min="1" max="20" data-field="max_episodes" value="${feed.max_episodes ?? 1}"></div>
        <div class="feed-cfg-row"><label>Instructions</label>
          <textarea class="feed-cfg-input" data-field="instructions" placeholder="Optional prompt...">${escapeHtml(feed.instructions || '')}</textarea></div>
        <div class="feed-card-btns">
          <button class="feed-cfg-del" style="padding:7px 12px;font-size:12px;" data-idx="${idx}">Remove</button>
          <button class="btn feed-card-cancel-btn" style="flex:1;justify-content:center;">Cancel</button>
          <button class="btn btn-accent feed-card-save-btn" style="flex:1;justify-content:center;">Save</button>
        </div>
      </div>`;
    const urlInput = card.querySelector('[data-field="url"]');
    urlInput.addEventListener('blur', () => _applyUrlNormalization(urlInput));
    if (feed.url) _applyUrlNormalization(urlInput);
    const editFields = card.querySelector('.feed-card-edit-fields');
    const editBtn = card.querySelector('.feed-card-edit-btn');
    const lockEl = card.querySelector('.feed-card-lock');
    const allInputs = card.querySelectorAll('.feed-cfg-input, .feed-cfg-select');

    editBtn.addEventListener('click', () => {
      editFields.classList.add('active');
      editBtn.style.display = 'none';
      allInputs.forEach(el => { el.disabled = true; });
      lockEl.style.cursor = 'pointer';
    });

    lockEl.addEventListener('click', () => {
      if (!editFields.classList.contains('active')) return;
      const locked = allInputs[0]?.disabled;
      allInputs.forEach(el => { el.disabled = !locked; });
      lockEl.innerHTML = locked ? SVG_UNLOCKED : SVG_LOCKED;
    });

    card.querySelector('.feed-card-cancel-btn').addEventListener('click', () => {
      editFields.classList.remove('active');
      editBtn.style.display = '';
      allInputs.forEach(el => { el.disabled = false; });
      lockEl.innerHTML = SVG_LOCKED;
      lockEl.style.cursor = '';
    });
    card.querySelector('.feed-card-save-btn').addEventListener('click', () => {
      _saveFeedCard(card, idx, publishedMap);
    });
    card.querySelector('.feed-cfg-del').addEventListener('click', () => {
      _feedCfgData.splice(idx, 1);
      _renderFeedCfg(publishedMap);
    });
    card.querySelector('.feed-card-copy-btn').addEventListener('click', (e) => {
      copyFeedUrl(e.currentTarget, rssUrl);
    });
    return card;
  }

  function _renderFeedCfg(publishedMap = {}) {
    const list = document.getElementById('feedList');
    list.innerHTML = '';
    _feedCfgData.forEach((feed, idx) => {
      const published = publishedMap[feed.name] || null;
      const card = _makeFeedCard(feed, idx, published, publishedMap);
      list.appendChild(card);
    });
  }

  function _collectFeedCfg() {
    const cards = document.querySelectorAll('#feedList .feed-card');
    _feedCfgData = Array.from(cards).map(card => {
      const get = f => card.querySelector(`[data-field="${f}"]`)?.value ?? '';
      return {
        name: get('name'),
        url: normalizeYouTubeUrl(get('url')).value,
        title: get('title'),
        style: get('style') || 'deep-dive',
        language: get('language') || 'en',
        max_episodes: (n => Number.isNaN(n) ? 1 : Math.max(1, n))(parseInt(get('max_episodes'), 10)),
        instructions: get('instructions'),
      };
    });
  }

  async function loadFeedCfg() {
    try {
      const [cfgData, feedsData] = await Promise.all([
        authFetch('/api/transformer-config').then(r => r.json()),
        authFetch('/api/feeds').then(r => r.json()).catch(() => []),
      ]);
      _feedCfgData = Array.isArray(cfgData) ? cfgData : [];
      const publishedMap = {};
      if (Array.isArray(feedsData)) {
        for (const f of feedsData) publishedMap[f.name] = { episode_count: f.episode_count, url: f.url };
      }
      _renderFeedCfg(publishedMap);
    } catch (err) {
      console.warn('loadFeedCfg failed:', err);
      _feedCfgData = [];
      _renderFeedCfg({});
    }
  }

  document.getElementById('feedCfgAddBtn').addEventListener('click', () => {
    _collectFeedCfg();
    _feedCfgData.push({ name: '', url: '', title: '', style: 'deep-dive', language: 'en', max_episodes: 1, instructions: '' });
    _renderFeedCfg();
    document.querySelector('#feedList .feed-card:last-child')?.scrollIntoView({ behavior: 'smooth' });
  });

  // ── Init ──────────────────────────────────────────
  initAuth().then(() => {
    setInterval(loadEpisodes, 30000);
  });

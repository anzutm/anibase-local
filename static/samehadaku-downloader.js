/**
 * static/samehadaku-downloader.js
 * Frontend logic for Samehadaku episode downloader in AniBase.
 * Mendukung Rilisan Episode Terbaru secara instan saat modal dibuka,
 * pencarian global anime, dan pemilihan resolusi unduhan langsung.
 */

(function () {
    'use strict';

    let currentAnimeName = '';
    let currentAnimeHubUrl = '';
    let currentSeriesResults = [];
    let latestEpisodesList = [];
    let lastActiveView = 'latest'; // 'latest' | 'search' | 'details' | 'quick_ep'
    let currentProvider = 'samehadaku'; // 'samehadaku' | 'lendrive'
    let currentAnimeDetailsData = null;
    let pollTimer = null;
    let localEpisodeNumbers = new Set();
    const downloadTiersRegistry = {};

    // Helper pendeteksi provider berdasarkan URL
    function getProviderForUrl(url) {
        if (!url) return currentProvider;
        if (typeof url === 'string') {
            if (url.includes('lendrive.web.id')) return 'lendrive';
            if (url.includes('samehadaku')) return 'samehadaku';
        }
        return currentProvider;
    }

    // Dapatkan action token untuk otorisasi POST request
    function getActionToken() {
        if (window.ANIBASE_ACTION_TOKEN) return window.ANIBASE_ACTION_TOKEN;
        const meta = document.querySelector('meta[name="action-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="action_token"]');
        if (input && input.value) return input.value;
        return window.__ACTION_TOKEN__ || '';
    }

    // Helper format ukuran byte ke KB/MB/GB
    function formatBytes(bytes) {
        if (!bytes || bytes <= 0) return '0 B';
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + sizes[i];
    }

    // Helper format kecepatan unduh
    function formatSpeed(bps) {
        if (!bps || bps <= 0) return '0 KB/s';
        return formatBytes(bps) + '/s';
    }

    // Buat elemen modal jika belum ada
    function ensureModalDOM() {
        let modal = document.getElementById('samehadakuModal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.id = 'samehadakuModal';
        modal.className = 'samehadaku-modal';
        modal.hidden = true;

        modal.innerHTML = `
            <div class="samehadaku-backdrop" data-samehadaku-close></div>
            <div class="samehadaku-dialog" role="dialog" aria-modal="true" aria-labelledby="samehadakuModalTitle">
                <header class="samehadaku-header">
                    <div>
                        <span class="samehadaku-eyebrow" id="samehadakuEyebrow">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2L1 21h22L12 2zm0 3.99L19.53 19H4.47L12 5.99zM11 10h2v4h-2zm0 6h2v2h-2z"/></svg>
                            Samehadaku Integration
                        </span>
                        <h2 id="samehadakuModalTitle">Download Anime Episodes</h2>
                        <p id="samehadakuModalSubtitle">Unduh episode langsung dari Samehadaku ke library AniBase</p>
                    </div>
                    <div class="samehadaku-header-actions">
                        <button type="button" class="samehadaku-close-btn" data-samehadaku-close aria-label="Tutup">&times;</button>
                    </div>
                </header>

                <div class="samehadaku-provider-tabs" id="samehadakuProviderTabs">
                    <button type="button" class="samehadaku-provider-btn active" data-provider-val="samehadaku" title="Pilih sumber Samehadaku (Sub Indo)">
                        <span class="provider-pill-icon">🐟</span>
                        <div class="provider-pill-text">
                            <strong>Samehadaku</strong>
                            <small>Sub Indo</small>
                        </div>
                    </button>
                    <button type="button" class="samehadaku-provider-btn" data-provider-val="lendrive" title="Pilih sumber Lendrive (x265 HEVC DualSubs)">
                        <span class="provider-pill-icon">⚡</span>
                        <div class="provider-pill-text">
                            <strong>Lendrive</strong>
                            <small>x265 HEVC · DualSubs</small>
                        </div>
                    </button>
                </div>

                <div class="samehadaku-search-row">
                    <input type="search" id="samehadakuSearchInput" placeholder="Cari judul anime di Samehadaku..." autocomplete="off">
                    <button type="button" id="samehadakuSearchBtn">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                        <span>Cari</span>
                    </button>
                </div>

                <div class="samehadaku-modal-tabs">
                    <button type="button" class="samehadaku-modal-tab-btn active" id="modalTabLatest">
                        <span>🔥 Rilisan Terbaru</span>
                    </button>
                    <button type="button" class="samehadaku-modal-tab-btn" id="modalTabTracker">
                        <span>📡 Pantau Ongoing</span>
                        <span class="samehadaku-modal-tab-badge" id="modalTrackerBadge" hidden>0</span>
                    </button>
                    <button type="button" class="samehadaku-modal-tab-btn samehadaku-cookie-tab-btn" id="modalTabCookieBtn" onclick="window.__setLendriveCookiePrompt()" title="Pengaturan Cookie Sesi Lendrive" style="display: none; margin-left: auto;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
                        <span>⚙️ Cookie Lendrive</span>
                    </button>
                </div>

                <div class="samehadaku-body" id="samehadakuModalBody">
                    <div class="samehadaku-status-box" id="samehadakuStatusBox">
                        <div class="samehadaku-spinner"></div>
                        <div>Memuat rilisan episode anime terbaru...</div>
                    </div>
                </div>

                <div class="samehadaku-downloads-tray" id="samehadakuTray" hidden>
                    <div class="samehadaku-downloads-tray-header">
                        <div class="samehadaku-tray-header-left">
                            <h4>Unduhan Aktif / Riwayat</h4>
                            <span class="samehadaku-tray-queue-indicator" id="samehadakuQueueIndicator"></span>
                        </div>
                        <div class="samehadaku-tray-header-actions">
                            <label class="samehadaku-tray-limit-label" for="samehadakuQueueLimitSelect">Batas Paralel:</label>
                            <select id="samehadakuQueueLimitSelect" class="samehadaku-tray-limit-select" title="Batas kuota unduhan bersamaan">
                                <option value="1">1 Unduhan</option>
                                <option value="2">2 Unduhan</option>
                                <option value="3">3 Unduhan</option>
                                <option value="5">5 Unduhan</option>
                            </select>
                            <button type="button" class="samehadaku-tray-clear-btn" id="samehadakuClearTrayBtn">Bersihkan yang selesai</button>
                        </div>
                    </div>
                    <div id="samehadakuTasksList"></div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Pasang event listener modal
        modal.querySelectorAll('[data-samehadaku-close]').forEach(el => {
            el.addEventListener('click', closeSamehadakuModal);
        });

        // Event listener pemilihan sumber (Samehadaku vs Lendrive)
        modal.querySelectorAll('[data-provider-val]').forEach(btn => {
            btn.addEventListener('click', () => {
                const targetProvider = btn.getAttribute('data-provider-val');
                if (targetProvider && targetProvider !== currentProvider) {
                    switchProvider(targetProvider, true);
                }
            });
        });

        const searchBtn = modal.querySelector('#samehadakuSearchBtn');
        const searchInput = modal.querySelector('#samehadakuSearchInput');
        if (searchBtn && searchInput) {
            searchBtn.addEventListener('click', () => {
                triggerSearch(searchInput.value.trim());
            });
            searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    triggerSearch(searchInput.value.trim());
                }
            });
            searchInput.addEventListener('input', () => {
                // Jika input dikosongkan dan kita tidak di view latest, kembalikan ke latest
                if (!searchInput.value.trim() && lastActiveView !== 'latest') {
                    loadLatestReleases();
                }
            });
        }

        const clearBtn = modal.querySelector('#samehadakuClearTrayBtn');
        if (clearBtn) {
            clearBtn.addEventListener('click', clearCompletedTasks);
        }

        const limitSelect = modal.querySelector('#samehadakuQueueLimitSelect');
        if (limitSelect) {
            limitSelect.addEventListener('change', (e) => {
                window.__setDownloadQueueLimit(e.target.value);
            });
        }

        const tabLatest = modal.querySelector('#modalTabLatest');
        const tabTracker = modal.querySelector('#modalTabTracker');
        if (tabLatest && tabTracker) {
            tabLatest.addEventListener('click', () => {
                tabLatest.classList.add('active');
                tabTracker.classList.remove('active');
                loadLatestReleases();
            });
            tabTracker.addEventListener('click', () => {
                tabTracker.classList.add('active');
                tabLatest.classList.remove('active');
                loadOngoingTrackerView();
            });
        }

        return modal;
    }

    // Deteksi nomor episode lokal yang sudah ada di halaman anime.html
    function detectLocalEpisodes() {
        localEpisodeNumbers.clear();
        const epCards = document.querySelectorAll('[data-episode-number]');
        epCards.forEach(card => {
            const num = card.getAttribute('data-episode-number');
            if (num) localEpisodeNumbers.add(String(num).trim());
        });
    }

    // Ganti provider sumber unduhan (Samehadaku vs Lendrive)
    function switchProvider(newProvider, preserveQuery = true) {
        if (newProvider !== 'samehadaku' && newProvider !== 'lendrive') newProvider = 'samehadaku';
        currentProvider = newProvider;

        const modal = document.getElementById('samehadakuModal');
        if (!modal) return;

        // Perbarui highlight tombol tab provider
        modal.querySelectorAll('[data-provider-val]').forEach(btn => {
            if (btn.getAttribute('data-provider-val') === currentProvider) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        const isLendrive = currentProvider === 'lendrive';
        const eyebrow = modal.querySelector('#samehadakuEyebrow');
        const subtitle = modal.querySelector('#samehadakuModalSubtitle');
        const searchInput = modal.querySelector('#samehadakuSearchInput');
        const cookieBtnTab = modal.querySelector('#modalTabCookieBtn');

        if (cookieBtnTab) cookieBtnTab.style.display = isLendrive ? 'inline-flex' : 'none';

        if (eyebrow) {
            eyebrow.innerHTML = isLendrive
                ? `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg> Lendrive Integration (x265 HEVC)`
                : `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2L1 21h22L12 2zm0 3.99L19.53 19H4.47L12 5.99zM11 10h2v4h-2zm0 6h2v2h-2z"/></svg> Samehadaku Integration`;
        }

        if (subtitle) {
            subtitle.textContent = isLendrive
                ? 'Unduh rilisan anime x265 / HEVC berkualitas tinggi dengan Dual Subtitle dari Lendrive'
                : 'Unduh episode langsung dari Samehadaku ke library AniBase';
        }

        if (searchInput) {
            searchInput.placeholder = isLendrive
                ? 'Cari judul anime di Lendrive (x265 / HEVC DualSubs)...'
                : 'Cari judul anime di Samehadaku...';
        }

        // Cek query pencarian atau reload view
        const q = searchInput ? searchInput.value.trim() : '';
        if (q && preserveQuery) {
            triggerSearch(q);
        } else {
            const tabLatest = modal.querySelector('#modalTabLatest');
            const tabTracker = modal.querySelector('#modalTabTracker');
            if (tabLatest) tabLatest.classList.add('active');
            if (tabTracker) tabTracker.classList.remove('active');
            loadLatestReleases();
        }
    }

    // Buka modal
    function openSamehadakuModal(animeTitle, initialQuery, preferredProvider = 'samehadaku') {
        const modal = ensureModalDOM();
        modal.hidden = false;
        document.body.classList.add('samehadaku-modal-open');

        detectLocalEpisodes();

        currentAnimeName = animeTitle || '';
        const searchInput = modal.querySelector('#samehadakuSearchInput');
        const query = initialQuery || animeTitle || '';

        // Terapkan provider yang diminta tombol
        if (preferredProvider && (preferredProvider === 'samehadaku' || preferredProvider === 'lendrive')) {
            currentProvider = preferredProvider;
            modal.querySelectorAll('[data-provider-val]').forEach(btn => {
                if (btn.getAttribute('data-provider-val') === currentProvider) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            const isLendrive = currentProvider === 'lendrive';
            const eyebrow = modal.querySelector('#samehadakuEyebrow');
            const subtitle = modal.querySelector('#samehadakuModalSubtitle');
            if (eyebrow) {
                eyebrow.innerHTML = isLendrive
                    ? `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg> Lendrive Integration (x265 HEVC)`
                    : `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2L1 21h22L12 2zm0 3.99L19.53 19H4.47L12 5.99zM11 10h2v4h-2zm0 6h2v2h-2z"/></svg> Samehadaku Integration`;
            }
            if (subtitle) {
                subtitle.textContent = isLendrive
                    ? 'Unduh rilisan anime x265 / HEVC berkualitas tinggi dengan Dual Subtitle dari Lendrive'
                    : 'Unduh episode langsung dari Samehadaku ke library AniBase';
            }
            if (searchInput) {
                searchInput.placeholder = isLendrive
                    ? 'Cari judul anime di Lendrive (x265 / HEVC DualSubs)...'
                    : 'Cari judul anime di Samehadaku...';
            }
        }

        const isLendriveNow = currentProvider === 'lendrive';
        const cookieBtnTab = modal.querySelector('#modalTabCookieBtn');
        if (cookieBtnTab) cookieBtnTab.style.display = isLendriveNow ? 'inline-flex' : 'none';

        if (searchInput) {
            searchInput.value = query;
            if (query) {
                triggerSearch(query);
            } else {
                loadLatestReleases();
            }
        } else {
            loadLatestReleases();
        }

        // Mulai polling status unduhan jika ada
        startStatusPolling();
    }

    // Tutup modal
    function closeSamehadakuModal() {
        const modal = document.getElementById('samehadakuModal');
        if (modal) {
            modal.hidden = true;
            document.body.classList.remove('samehadaku-modal-open');
        }
        stopStatusPolling();
    }

    // Render loading spinner
    function showLoading(msg) {
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;
        body.innerHTML = `
            <div class="samehadaku-status-box">
                <div class="samehadaku-spinner"></div>
                <div>${msg || 'Memuat data dari Samehadaku...'}</div>
            </div>
        `;
    }

    // Render status box
    function showMessage(msg, isError) {
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;
        body.innerHTML = `
            <div class="samehadaku-status-box" style="${isError ? 'color: #ef4444;' : ''}">
                ${msg}
            </div>
        `;
    }

    // Render pesan khusus proteksi Anti-Bot WAF Lendrive
    function renderLendriveProtectedMessage(cookieConfigured) {
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;
        body.innerHTML = `
            <div class="samehadaku-status-box" style="padding: 24px 18px; max-width: 560px; margin: 0 auto; text-align: center;">
                <div style="font-size: 36px; margin-bottom: 10px;">🛡️</div>
                <h3 style="font-size: 16px; font-weight: 700; color: #fbbf24; margin: 0 0 10px;">
                    Verifikasi Anti-Bot (WAF Protection) Aktif di Lendrive
                </h3>
                <p style="color: var(--text-secondary, #94a3b8); font-size: 13px; line-height: 1.6; margin: 0 0 18px;">
                    Situs <strong>lendrive.web.id</strong> saat ini sedang memproteksi akses halamannya dengan gerbang verifikasi browser otomatis (<em>One moment, please... Please wait while your request is being verified</em>).<br><br>
                    Karena AniBase backend tidak menjalankan eksekusi browser/JavaScript untuk membobol proteksi ini, Anda dapat membuka Lendrive langsung di browser atau menempelkan Cookie sesi browser di bawah.
                </p>
                <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 10px;">
                    <a href="https://lendrive.web.id" target="_blank" rel="noopener noreferrer" class="samehadaku-batch-btn-primary" style="display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px; text-decoration: none;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                        <span>Buka Lendrive di Browser</span>
                    </a>
                    <button type="button" class="samehadaku-batch-btn-secondary" onclick="window.__setLendriveCookiePrompt()" style="display: inline-flex; align-items: center; gap: 6px; padding: 9px 16px;">
                        <span>⚙️ ${cookieConfigured ? 'Perbarui Cookie Sesi' : 'Masukkan Cookie Browser'}</span>
                    </button>
                    <button type="button" class="samehadaku-batch-btn-secondary" onclick="window.__retryLendrive()" style="display: inline-flex; align-items: center; gap: 6px; padding: 9px 14px;">
                        <span>🔄 Coba Lagi</span>
                    </button>
                </div>
            </div>
        `;
    }

    window.__retryLendrive = function() {
        loadLatestReleases(1);
    };

    window.__setLendriveCookiePrompt = async function () {
        const val = prompt(
            'Petunjuk mendapatkan Cookie Sesi Lendrive:\n\n' +
            '1. Buka https://lendrive.web.id di browser (selesaikan verifikasi bot jika muncul).\n' +
            '2. Tekan F12 -> tab Application (atau Storage) -> Cookies -> https://lendrive.web.id\n' +
            '3. Salin Value dari cookie "wssplashchk" (atau tempel format: wssplashchk=...)\n\n' +
            'Tempel / Masukkan Cookie di sini (kosongkan lalu klik OK untuk mereset):',
            ''
        );
        if (val === null) return;
        const token = getActionToken();
        try {
            const res = await fetch('/api/lendrive/cookie', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({ cookie: val.trim(), action_token: token })
            });
            const data = await res.json();
            if (data && data.ok) {
                alert(val.trim() ? '✓ Cookie Lendrive berhasil disimpan! Memuat ulang rilisan...' : '✓ Cookie Lendrive telah direset.');
                loadLatestReleases(1);
            } else {
                alert(data.error || 'Gagal menyimpan cookie');
            }
        } catch (err) {
            alert('Terjadi kesalahan: ' + err.message);
        }
    };

    // Muat rilisan anime terbaru dari sumber aktif (Samehadaku vs Lendrive)
    async function loadLatestReleases(page = 1) {
        lastActiveView = 'latest';
        const providerName = currentProvider === 'lendrive' ? 'Lendrive (x265 HEVC)' : 'Samehadaku';
        showLoading(`Memuat rilisan episode anime terbaru dari ${providerName}...`);

        try {
            const res = await fetch(`/api/${currentProvider}/latest?page=${page}`);
            const data = await res.json();

            if (!data.ok || !data.results || data.results.length === 0) {
                if (currentProvider === 'lendrive' && data.is_protected) {
                    renderLendriveProtectedMessage(data.cookie_configured);
                    return;
                }
                showMessage(`Belum ada episode terbaru yang dapat dimuat dari ${providerName} saat ini. Silakan coba cari judul anime secara manual.`);
                return;
            }

            latestEpisodesList = data.results;
            renderLatestReleases(data.results, page);
        } catch (err) {
            showMessage(`Gagal memuat episode terbaru: ${err.message}`, true);
        }
    }

    // Render grid rilisan terbaru dengan thumbnail & badge episode
    function renderLatestReleases(episodes, page = 1) {
        lastActiveView = 'latest';
        const tabLatest = document.getElementById('modalTabLatest');
        const tabTracker = document.getElementById('modalTabTracker');
        if (tabLatest) tabLatest.classList.add('active');
        if (tabTracker) tabTracker.classList.remove('active');

        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const isLendrive = currentProvider === 'lendrive';
        const subtitleText = isLendrive
            ? 'Episode anime baru rilis dari Lendrive (x265 / HEVC DualSubs). Klik untuk memilih resolusi download atau melihat serial.'
            : 'Episode anime baru rilis dari Samehadaku. Klik untuk memilih resolusi download atau melihat serial.';

        let html = `
            <div class="samehadaku-section-header">
                <div class="samehadaku-section-title-wrap">
                    <span class="samehadaku-section-title">
                        <span>🔥</span> Rilisan Episode Terbaru (${isLendrive ? 'Lendrive HEVC' : 'Samehadaku'})
                    </span>
                    <span class="samehadaku-section-subtitle">
                        ${subtitleText}
                    </span>
                </div>
                <div class="samehadaku-section-badge">
                    ${episodes.length} Episode Baru
                </div>
            </div>
            <div class="samehadaku-latest-grid">
        `;

        episodes.forEach((ep, idx) => {
            const thumbUrl = ep.thumbnail || '/static/arcana.jpg';
            const epLabel = ep.episode || 'Episode Baru';
            const timeLabel = ep.released_on || '';
            const epNum = ep.episode_number || '';
            const isLocal = epNum && localEpisodeNumbers.has(String(epNum));
            const targetUrl = ep.episode_url || ep.url || '';
            const formatBadge = ep.format_badge ? `<span class="samehadaku-badge-hevc">${ep.format_badge}</span>` : '';

            html += `
                <div class="samehadaku-latest-card" data-idx="${idx}">
                    <div class="samehadaku-latest-thumb-wrap" onclick="window.__quickDownloadEpisode('${encodeURIComponent(targetUrl)}', '${encodeURIComponent(ep.title)}', '${epNum}')" title="Klik untuk unduh ${ep.title} ${epLabel}">
                        <img class="samehadaku-latest-thumb" src="${thumbUrl}" alt="${ep.title}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/arcana.jpg'">
                        <div class="samehadaku-latest-overlay"></div>
                        <span class="samehadaku-latest-ep-badge">${epLabel}</span>
                        ${timeLabel ? `<span class="samehadaku-latest-time-badge">${timeLabel}</span>` : ''}
                    </div>
                    <div class="samehadaku-latest-content">
                        <div class="samehadaku-latest-title" onclick="window.__quickDownloadEpisode('${encodeURIComponent(targetUrl)}', '${encodeURIComponent(ep.title)}', '${epNum}')" title="${ep.title}">
                            ${ep.title}
                            ${formatBadge}
                        </div>
                        <div class="samehadaku-latest-actions">
                            <button type="button" class="samehadaku-btn-download-ep" onclick="window.__quickDownloadEpisode('${encodeURIComponent(targetUrl)}', '${encodeURIComponent(ep.title)}', '${epNum}')">
                                <span>⚡</span> Unduh
                            </button>
                            ${(ep.anime_url || ep.url) ? `
                                <button type="button" class="samehadaku-btn-view-series" onclick="window.__viewAnimeSeries('${encodeURIComponent(ep.anime_url || ep.url)}', '${encodeURIComponent(ep.title)}')" title="Lihat semua episode serial ini">
                                    Serial
                                </button>
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        body.innerHTML = html;
    }

    // Muat tampilan Ongoing Watchlist Tracker
    async function loadOngoingTrackerView() {
        lastActiveView = 'tracker';
        const tabLatest = document.getElementById('modalTabLatest');
        const tabTracker = document.getElementById('modalTabTracker');
        if (tabTracker) tabTracker.classList.add('active');
        if (tabLatest) tabLatest.classList.remove('active');

        showLoading('Memuat daftar anime ongoing yang dipantau...');

        try {
            const res = await fetch('/api/ongoing-tracker/list');
            const data = await res.json();

            if (!data.ok || !data.tracked_anime) {
                showMessage('Gagal memuat daftar anime ongoing yang dipantau.', true);
                return;
            }

            renderOngoingTrackerView(data.tracked_anime);
        } catch (err) {
            showMessage(`Gagal memuat data ongoing tracker: ${err.message}`, true);
        }
    }

    // Render daftar kartu Ongoing Watchlist
    function renderOngoingTrackerView(trackedList) {
        lastActiveView = 'tracker';
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const tabLatest = document.getElementById('modalTabLatest');
        const tabTracker = document.getElementById('modalTabTracker');
        if (tabTracker) tabTracker.classList.add('active');
        if (tabLatest) tabLatest.classList.remove('active');

        const withNew = (trackedList || []).filter(item => item.has_new_episode);
        const modalBadge = document.getElementById('modalTrackerBadge');
        if (modalBadge) {
            if (withNew.length > 0) {
                modalBadge.hidden = false;
                modalBadge.textContent = String(withNew.length);
                modalBadge.classList.add('has-new');
            } else {
                modalBadge.hidden = true;
                modalBadge.classList.remove('has-new');
            }
        }

        if (!trackedList || trackedList.length === 0) {
            body.innerHTML = `
                <div class="samehadaku-status-box">
                    <p style="font-size: 15px; font-weight: 700; margin-bottom: 8px;">Belum ada anime ongoing yang dipantau</p>
                    <p style="color: var(--text-secondary, #94a3b8); font-size: 13px; max-width: 440px; margin: 0 auto 16px;">
                        Folder anime di direktori Ongoing atau anime dengan status Airing akan otomatis terdeteksi. Anda juga bisa menandai anime dari halaman detail.
                    </p>
                    <button type="button" class="samehadaku-batch-btn-primary" onclick="window.__checkOngoingReleases()" style="padding: 8px 18px;">
                        🔄 Pindai & Periksa Sekarang
                    </button>
                </div>
            `;
            return;
        }

        // Urutkan anime: yang ada episode baru di paling atas
        trackedList.sort((a, b) => {
            if (a.has_new_episode && !b.has_new_episode) return -1;
            if (!a.has_new_episode && b.has_new_episode) return 1;
            return a.anime_name.localeCompare(b.anime_name);
        });

        let html = `
            <div class="samehadaku-section-header">
                <div class="samehadaku-section-title-wrap">
                    <span class="samehadaku-section-title">
                        <span>📡</span> Ongoing Watchlist Tracker
                    </span>
                    <span class="samehadaku-section-subtitle">
                        Memantau rilis episode terbaru Samehadaku untuk anime yang sedang Anda ikuti.
                    </span>
                </div>
                <div class="samehadaku-section-badge">
                    ${trackedList.length} Anime Dipantau
                </div>
            </div>

            <div class="samehadaku-tracker-toolbar">
                <div class="samehadaku-tracker-toolbar-info">
                    ${withNew.length > 0 ? `<strong>✨ ${withNew.length} Anime</strong> memiliki episode baru yang belum diunduh!` : `Semua koleksi lokal up-to-date.`}
                </div>
                <div class="samehadaku-tracker-toolbar-actions">
                    <button type="button" class="samehadaku-tracker-check-btn" id="btnModalTrackerCheck" onclick="window.__checkOngoingReleases()">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                        <span>Periksa Rilis Sekarang</span>
                    </button>
                </div>
            </div>

            <div class="samehadaku-tracker-grid">
        `;

        trackedList.forEach((item) => {
            const hasNew = item.has_new_episode;
            const localMax = item.highest_local_ep || 0;
            const sameMax = item.latest_samehadaku_ep || 0;
            const missing = item.missing_episodes || [];
            const nextEpToDownload = missing.length > 0 ? missing[0] : (sameMax > localMax ? sameMax : null);

            let localText = 'Belum Ada';
            if (item.local_episodes && item.local_episodes.length > 0) {
                const minEp = Math.min(...item.local_episodes);
                const maxEp = Math.max(...item.local_episodes);
                if (minEp === maxEp) {
                    localText = `Ep ${minEp}`;
                } else {
                    localText = `Ep ${minEp} - ${maxEp}`;
                }
                if (item.local_episodes.length > 1) {
                    localText += ` (${item.local_episodes.length} ep)`;
                }
            } else if (localMax > 0) {
                localText = `Ep 1 - ${localMax}`;
            }

            html += `
                <div class="samehadaku-tracker-card ${hasNew ? 'has-new' : ''}">
                    <div class="samehadaku-tracker-card-head">
                        <h4 class="samehadaku-tracker-card-title" title="${item.anime_name}">${item.anime_name}</h4>
                        <div class="samehadaku-tracker-card-badges">
                            <span class="samehadaku-tracker-card-badge ${hasNew ? 'new-ep' : 'up-to-date'}">
                                ${hasNew ? `✨ Ep ${nextEpToDownload} Rilis!` : '✓ Lengkap'}
                            </span>
                            <button type="button" class="samehadaku-tracker-card-remove" title="Hapus dari Pantauan Ongoing" onclick="window.__removeOngoingTracker('${encodeURIComponent(item.anime_name)}')">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <line x1="18" y1="6" x2="6" y2="18"></line>
                                    <line x1="6" y1="6" x2="18" y2="18"></line>
                                </svg>
                            </button>
                        </div>
                    </div>

                    <div class="samehadaku-tracker-card-meta">
                        <div class="samehadaku-tracker-card-row">
                            <span>Koleksi Lokal:</span>
                            <strong>${localText}</strong>
                        </div>
                        <div class="samehadaku-tracker-card-row">
                            <span>Rilis Samehadaku:</span>
                            <strong>${sameMax > 0 ? `Ep ${sameMax}` : 'Belum Terdeteksi'}</strong>
                        </div>
                    </div>

                    <div class="samehadaku-tracker-card-actions">
                        ${hasNew && nextEpToDownload ? `
                            <button type="button" class="samehadaku-tracker-btn-dl" onclick="window.__startOngoingQuickDownload('${encodeURIComponent(item.anime_name)}', '${encodeURIComponent(item.samehadaku_url || '')}', '${nextEpToDownload}')">
                                ⬇️ Unduh Ep ${nextEpToDownload}
                            </button>
                        ` : ''}
                        <button type="button" class="samehadaku-tracker-btn-view" onclick="window.__searchFromTracker('${encodeURIComponent(item.anime_name)}')">
                            Buka Serial
                        </button>
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        body.innerHTML = html;
    }

    // Unduh cepat episode tertentu langsung dari kartu latest releases
    window.__quickDownloadEpisode = async function (encodedEpUrl, encodedTitle, epNumber) {
        const epUrl = decodeURIComponent(encodedEpUrl);
        const title = decodeURIComponent(encodedTitle);
        // Bersihkan kata 'Episode X' atau akhiran tanda hubung dari judul anime agar nama serial bersih
        const cleanAnime = title
            .replace(/(?:\s*-\s*)?(?:episode|episodes|eps?|e)\s*\d+.*$/i, '')
            .replace(/\s*[-–—]\s*\d{1,4}(?:\.\d+)?\s*$/i, '')
            .replace(/[\s\-–—]+$/, '')
            .trim();
        currentAnimeName = cleanAnime || title;
        lastActiveView = 'quick_ep';

        const provider = getProviderForUrl(epUrl);
        const providerName = provider === 'lendrive' ? 'Lendrive' : 'Samehadaku';
        const displayEp = epNumber ? `Episode ${epNumber}` : 'Episode';
        showLoading(`Mengambil opsi resolusi unduhan untuk "${currentAnimeName}" ${displayEp} dari ${providerName}...`);

        try {
            const res = await fetch(`/api/${provider}/downloads?url=${encodeURIComponent(epUrl)}`);
            const data = await res.json();

            if (!data.ok || !data.downloads || data.downloads.length === 0) {
                showMessage(`Tidak ditemukan tautan unduhan untuk episode ini pada ${providerName}. Silakan coba cari judul serialnya secara manual.`, true);
                return;
            }

            renderQuickEpisodeDownloads(epUrl, currentAnimeName, epNumber, data.downloads);
        } catch (err) {
            showMessage(`Gagal memuat link unduhan: ${err.message}`, true);
        }
    };

    // Render tampilan unduhan cepat untuk episode tunggal
    function renderQuickEpisodeDownloads(epUrl, title, epNumber, downloads) {
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const epDisplay = epNumber ? `Episode ${epNumber}` : 'Unduhan Episode';
        let html = `
            <div class="samehadaku-episodes-header">
                <div class="samehadaku-episodes-nav-bar">
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()" title="Kembali">
                        <svg class="samehadaku-back-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M19 12H5M12 19l-7-7 7-7"/>
                        </svg>
                        <span>Kembali</span>
                    </button>
                    <div class="samehadaku-section-badge">
                        Pilihan Unduhan
                    </div>
                </div>
                <div class="samehadaku-episodes-title-wrap">
                    <h3 class="samehadaku-episodes-anime-title">${title} &bull; ${epDisplay}</h3>
                </div>
            </div>
            <div class="samehadaku-ep-item" style="border-color: color-mix(in srgb, var(--primary) 50%, transparent); box-shadow: 0 4px 20px var(--card-glow);">
                <div class="samehadaku-ep-details" style="display:block;">
        `;

        html += generateDownloadTiersHTML(downloads, epNumber || '1', 'quick');

        html += `
                </div>
            </div>
        `;
        body.innerHTML = html;
    }

    // Lihat semua episode anime dari hub
    window.__viewAnimeSeries = function (encodedAnimeUrl, encodedTitle) {
        const url = decodeURIComponent(encodedAnimeUrl);
        const title = decodeURIComponent(encodedTitle);
        loadAnimeDetails(url, title);
    };

    // Navigasi kembali
    window.__backToPreviousView = function () {
        if (lastActiveView === 'tracker') {
            loadOngoingTrackerView();
        } else if (lastActiveView === 'search' && currentSeriesResults.length > 0) {
            renderSearchResults(currentSeriesResults);
        } else if (latestEpisodesList.length > 0) {
            renderLatestReleases(latestEpisodesList);
        } else {
            loadLatestReleases();
        }
    };

    // Pencarian ke server AniBase
    async function triggerSearch(query) {
        if (!query) {
            loadLatestReleases();
            return;
        }

        lastActiveView = 'search';
        const providerName = currentProvider === 'lendrive' ? 'Lendrive' : 'Samehadaku';
        showLoading(`Mencari "${query}" di ${providerName}...`);

        try {
            const res = await fetch(`/api/${currentProvider}/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();

            if (!data.ok || !data.results || data.results.length === 0) {
                if (currentProvider === 'lendrive' && data.is_protected) {
                    renderLendriveProtectedMessage(false);
                    return;
                }
                showMessage(`
                    Tidak ditemukan hasil untuk "<strong>${query}</strong>" di ${providerName}. Coba kata kunci yang lebih singkat.<br><br>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()" title="Kembali ke Rilisan Terbaru">
                        <svg class="samehadaku-back-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M19 12H5M12 19l-7-7 7-7"/>
                        </svg>
                        <span>Kembali ke Rilisan Terbaru</span>
                    </button>
                `);
                return;
            }

            // Filter defensif: pastikan hanya serial anime dan batch yang ditampilkan (bukan postingan episode)
            const animeOnly = (data.results || []).filter(r => !r.is_episode || r.is_anime || r.is_batch);
            const finalResults = (animeOnly.length > 0) ? animeOnly : data.results;

            currentSeriesResults = finalResults;

            // Jika hanya ada 1 hasil yang merupakan anime hub, langsung buka detailnya
            const animeHubs = finalResults.filter(r => r.is_anime);
            if (animeHubs.length === 1 && !finalResults[0].is_episode) {
                loadAnimeDetails(animeHubs[0].url, animeHubs[0].title);
            } else {
                renderSearchResults(finalResults);
            }
        } catch (err) {
            showMessage(`Gagal melakukan pencarian: ${err.message}`, true);
        }
    }

    // Render kartu hasil pencarian
    function renderSearchResults(results) {
        lastActiveView = 'search';
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const providerLabel = currentProvider === 'lendrive' ? 'Lendrive' : 'Samehadaku';

        let html = `
            <div class="samehadaku-section-header">
                <div>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()" title="Kembali ke Rilisan Terbaru">
                        <svg class="samehadaku-back-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M19 12H5M12 19l-7-7 7-7"/>
                        </svg>
                        <span>Kembali ke Rilisan Terbaru</span>
                    </button>
                    <div style="margin-top: 8px; font-size: 13px; color: var(--text-secondary, #A5B4D6);">
                        Ditemukan ${results.length} serial anime di ${providerLabel}. Pilih serial anime yang sesuai:
                    </div>
                </div>
            </div>
            <div class="samehadaku-series-list">
        `;

        results.forEach((item, idx) => {
            const typeLabel = item.is_batch ? 'Batch' : (item.type || 'Anime');
            const thumb = item.thumbnail || '';
            const formatBadge = item.format_badge ? `<span class="samehadaku-badge-hevc">${item.format_badge}</span>` : '';
            html += `
                <div class="samehadaku-series-card" data-idx="${idx}" onclick="window.__selectSamehadakuSeries(${idx})">
                    ${thumb ? `<img src="${thumb}" class="samehadaku-series-thumb" alt="${item.title}" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">` : ''}
                    <div class="samehadaku-series-title">${item.title}</div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap;">
                        <span class="samehadaku-series-badge">${typeLabel}</span>
                        ${formatBadge}
                        ${item.score ? `<span style="font-size:11px; color:var(--cyan, #fbbf24);">★ ${item.score}</span>` : ''}
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        body.innerHTML = html;
    }

    // Pilih serial dari hasil pencarian
    window.__selectSamehadakuSeries = function (idx) {
        const item = currentSeriesResults[idx];
        if (!item) return;
        loadAnimeDetails(item.url, item.title);
    };

    // Muat daftar episode dan detail anime
    async function loadAnimeDetails(pageUrl, title) {
        lastActiveView = 'details';
        showLoading(`Mengambil daftar episode untuk "${title}"...`);
        currentAnimeHubUrl = pageUrl;

        const provider = getProviderForUrl(pageUrl);

        try {
            const res = await fetch(`/api/${provider}/episodes?url=${encodeURIComponent(pageUrl)}&anime_name=${encodeURIComponent(title || currentAnimeName || '')}`);
            const data = await res.json();

            if (!data.ok) {
                showMessage(`Gagal memuat detail anime: ${data.error || 'Terjadi kesalahan'}`, true);
                return;
            }

            currentAnimeDetailsData = data;
            renderAnimeEpisodes(data);
        } catch (err) {
            showMessage(`Gagal memuat episode: ${err.message}`, true);
        }
    }

    // Render daftar episode
    function renderAnimeEpisodes(details) {
        lastActiveView = 'details';
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const episodes = details.episodes || [];
        const batchLinks = details.batch_links || [];
        const rawTitle = details.title || currentAnimeName || 'Detail Anime';
        const cleanTitle = rawTitle.replace(/[\s\-–—]+$/, '');
        currentAnimeName = cleanTitle;
        const posterFallback = details.poster || '/static/arcana.jpg';

        // Perbarui nomor episode lokal dari data pustaka
        const localLib = details.local_library || {};
        if (localLib.matched && Array.isArray(localLib.existing_episodes)) {
            localLib.existing_episodes.forEach(n => localEpisodeNumbers.add(String(n)));
        }

        // Klasifikasikan episode yang belum ada di library
        const missingEpisodes = [];
        episodes.forEach((ep, epIdx) => {
            const epNum = ep.episode || String(epIdx + 1);
            const isLocal = ep.is_local !== undefined ? ep.is_local : localEpisodeNumbers.has(String(epNum));
            ep._isLocal = isLocal;
            ep._epNum = epNum;
            if (!isLocal) {
                missingEpisodes.push({ ...ep, _origIdx: epIdx });
            }
        });

        let html = `
            <div class="samehadaku-episodes-header">
                <div class="samehadaku-episodes-nav-bar">
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()" title="Kembali ke tampilan sebelumnya">
                        <svg class="samehadaku-back-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M19 12H5M12 19l-7-7 7-7"/>
                        </svg>
                        <span>Kembali</span>
                    </button>
                    <div class="samehadaku-section-badge">
                        ${episodes.length} Episode Tersedia
                    </div>
                </div>
                <div class="samehadaku-episodes-title-wrap">
                    <h3 class="samehadaku-episodes-anime-title">${cleanTitle}</h3>
                </div>
            </div>
        `;

        // Banner Batch Download untuk episode yang belum ada
        if (missingEpisodes.length > 0) {
            html += `
                <div class="samehadaku-batch-banner" id="samehadakuBatchBanner">
                    <div class="samehadaku-batch-content">
                        <div class="samehadaku-batch-header-row">
                            <div class="samehadaku-batch-badge-wrap">
                                <span class="samehadaku-batch-badge">BATCH SYNC</span>
                                <span class="samehadaku-batch-title">Unduh Episode yang Belum Ada</span>
                            </div>
                            <div class="samehadaku-batch-count-info">
                                <strong>${missingEpisodes.length}</strong> dari <strong>${episodes.length}</strong> episode belum diunduh
                            </div>
                        </div>
                        <p class="samehadaku-batch-desc">
                            Menyinkronkan semua episode yang belum ada langsung ke folder library anime Anda secara berurutan.
                            ${currentProvider === 'lendrive' ? `
                                <span style="display: block; margin-top: 6px; font-size: 11.5px; color: #c084fc; line-height: 1.4;">
                                    ℹ️ <strong>Catatan Lendrive:</strong> Tautan episode Lendrive umumnya dilindungi safelink iklan (sfe.ad). Unduhan otomatis latar belakang hanya berjalan untuk link direct (Pixeldrain). Untuk episode bersafelink, klik tombol unduh per episode untuk membuka mirror di browser.
                                </span>
                            ` : ''}
                        </p>
                        <div class="samehadaku-batch-actions-row">
                            <div class="samehadaku-batch-controls-wrap">
                                <label for="samehadakuBatchQuality" class="samehadaku-batch-label">Kualitas:</label>
                                <select id="samehadakuBatchQuality" class="samehadaku-batch-select">
                                    <option value="1080p">1080p (MKV)</option>
                                    <option value="720p">720p (MKV)</option>
                                    <option value="480p">480p (MKV)</option>
                                </select>
                                <button type="button" class="samehadaku-batch-toggle-link" id="btnToggleAllBatchEps">Batal Pilih Semua</button>
                            </div>
                            <div class="samehadaku-batch-btn-group">
                                <button type="button" class="samehadaku-batch-btn-primary" id="btnStartBatchDownload">
                                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                                    <span id="btnStartBatchText">Unduh Semua (${missingEpisodes.length} Episode)</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        } else if (episodes.length > 0 && localLib.matched) {
            html += `
                <div class="samehadaku-all-synced-banner">
                    <span style="font-size:16px;">✓</span>
                    <span>Seluruh <strong>${episodes.length}</strong> episode sudah lengkap tersimpan di folder library Anda (<em>${localLib.anime_name || cleanTitle}</em>).</span>
                </div>
            `;
        }

        // Tampilkan batch links jika ada
        if (batchLinks.length > 0) {
            html += `
                <div style="margin-bottom: 16px; padding: 12px; border-radius: 14px; background: color-mix(in srgb, var(--primary) 12%, transparent); border: 1px solid color-mix(in srgb, var(--primary) 30%, transparent);">
                    <div style="font-size: 11px; font-weight: 800; color: var(--cyan, var(--primary)); margin-bottom: 8px; letter-spacing: 0.08em;">📦 BATCH DOWNLOAD TERSEDIA</div>
            `;
            batchLinks.forEach((b, bIdx) => {
                html += `
                    <div class="samehadaku-ep-item" style="margin-bottom: 6px;">
                        <div class="samehadaku-ep-summary" onclick="window.__toggleEpisodeLinks('batch-${bIdx}', '${encodeURIComponent(b.url)}')">
                            <div class="samehadaku-ep-thumb-wrap" style="display:flex; align-items:center; justify-content:center; background:color-mix(in srgb, var(--primary) 15%, #0f172a);">
                                <span style="font-size:18px;">📦</span>
                                <span class="samehadaku-ep-badge-num">BATCH</span>
                            </div>
                            <div class="samehadaku-ep-info">
                                <div class="samehadaku-ep-title-row">
                                    <span class="samehadaku-ep-title">${b.title}</span>
                                </div>
                                <div class="samehadaku-ep-meta-row">
                                    <span class="samehadaku-ep-status-hint">Paket unduhan lengkap satu season</span>
                                </div>
                            </div>
                            <div class="samehadaku-ep-action">
                                <button type="button" class="samehadaku-ep-expand-btn" id="btn-batch-${bIdx}">
                                    <span>⚡</span> Tampilkan Link
                                </button>
                            </div>
                        </div>
                        <div class="samehadaku-ep-details" id="details-batch-${bIdx}" hidden>
                            <div class="samehadaku-spinner" style="width:20px; height:20px; margin:0;"></div> Memuat tautan unduhan...
                        </div>
                    </div>
                `;
            });
            html += `</div>`;
        }

        if (episodes.length === 0) {
            html += `<div class="samehadaku-status-box">Tidak ada episode yang dapat diekstrak dari halaman ini.</div>`;
            body.innerHTML = html;
            return;
        }

        // Render setiap episode dengan thumbnail 16:9, nomor badge, dan aksi download
        episodes.forEach((ep, epIdx) => {
            const epNum = ep._epNum || ep.episode || String(epIdx + 1);
            const isLocal = ep._isLocal;
            const epThumb = ep.thumbnail || posterFallback;

            html += `
                <div class="samehadaku-ep-item ${!isLocal ? 'is-missing' : ''}" id="item-ep-${epIdx}">
                    <div class="samehadaku-ep-summary" onclick="window.__toggleEpisodeLinks('ep-${epIdx}', '${encodeURIComponent(ep.url)}', '${epNum}')">
                        <div class="samehadaku-ep-thumb-wrap">
                            <img class="samehadaku-ep-thumb" src="${epThumb}" alt="${ep.title}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${posterFallback}'">
                            <div class="samehadaku-ep-thumb-overlay">
                                <span class="samehadaku-ep-play-icon">⚡</span>
                            </div>
                            <span class="samehadaku-ep-badge-num">Ep ${epNum}</span>
                        </div>
                        <div class="samehadaku-ep-info">
                            <div class="samehadaku-ep-title-row">
                                <span class="samehadaku-ep-title">${ep.title}</span>
                                ${isLocal ? `<span class="samehadaku-ep-badge-local" title="Episode ini sudah ada di folder library Anda">✓ Di Library</span>` : `<span class="samehadaku-ep-badge-missing" title="Episode ini belum diunduh ke library">★ Belum Ada</span>`}
                            </div>
                            <div class="samehadaku-ep-meta-row">
                                ${ep.date ? `<span class="samehadaku-ep-date">📅 ${ep.date}</span>` : ''}
                                <span class="samehadaku-ep-status-hint">Klik untuk melihat pilihan resolusi unduhan</span>
                            </div>
                        </div>
                        <div class="samehadaku-ep-action" onclick="event.stopPropagation()">
                            ${!isLocal ? `
                                <label class="samehadaku-batch-checkbox-wrap" title="Pilih episode ini untuk unduh batch">
                                    <input type="checkbox" class="samehadaku-batch-checkbox" data-ep-idx="${epIdx}" checked>
                                    <span class="samehadaku-batch-checkbox-custom"></span>
                                </label>
                            ` : ''}
                            <button type="button" class="samehadaku-ep-expand-btn" id="btn-ep-${epIdx}" onclick="window.__toggleEpisodeLinks('ep-${epIdx}', '${encodeURIComponent(ep.url)}', '${epNum}')">
                                <span>⚡</span> Pilih Resolusi
                            </button>
                        </div>
                    </div>
                    <div class="samehadaku-ep-details" id="details-ep-${epIdx}" hidden>
                        <div style="display:flex; align-items:center; gap:8px;">
                            <div class="samehadaku-spinner" style="width:20px; height:20px; margin:0;"></div>
                            <span style="font-size:12px;">Memuat opsi unduhan...</span>
                        </div>
                    </div>
                </div>
            `;
        });

        body.innerHTML = html;

        // Pasang interaktivitas Batch Download jika ada episode yang belum diunduh
        if (missingEpisodes.length > 0) {
            const startBtn = body.querySelector('#btnStartBatchDownload');
            const startText = body.querySelector('#btnStartBatchText');
            const toggleLink = body.querySelector('#btnToggleAllBatchEps');
            const qualitySelect = body.querySelector('#samehadakuBatchQuality');
            const checkboxes = body.querySelectorAll('.samehadaku-batch-checkbox');

            function getSelectedEpisodes() {
                const selected = [];
                checkboxes.forEach(cb => {
                    if (cb.checked) {
                        const idx = parseInt(cb.getAttribute('data-ep-idx'), 10);
                        if (!isNaN(idx) && episodes[idx]) {
                            selected.push(episodes[idx]);
                        }
                    }
                });
                return selected;
            }

            function updateBatchButtonState() {
                const selected = getSelectedEpisodes();
                if (startBtn && startText) {
                    if (selected.length === 0) {
                        startBtn.disabled = true;
                        startText.textContent = 'Pilih Minimal 1 Episode';
                    } else if (selected.length === missingEpisodes.length) {
                        startBtn.disabled = false;
                        startText.textContent = `Unduh Semua (${selected.length} Episode)`;
                    } else {
                        startBtn.disabled = false;
                        startText.textContent = `Unduh Terpilih (${selected.length} Episode)`;
                    }
                }
                if (toggleLink) {
                    toggleLink.textContent = selected.length === 0 ? 'Pilih Semua' : 'Batal Pilih Semua';
                }
            }

            checkboxes.forEach(cb => {
                cb.addEventListener('change', updateBatchButtonState);
            });

            if (toggleLink) {
                toggleLink.addEventListener('click', () => {
                    const anyChecked = Array.from(checkboxes).some(cb => cb.checked);
                    checkboxes.forEach(cb => cb.checked = !anyChecked);
                    updateBatchButtonState();
                });
            }

            if (startBtn) {
                startBtn.addEventListener('click', () => {
                    const selected = getSelectedEpisodes();
                    if (selected.length === 0) return;
                    const qual = qualitySelect ? qualitySelect.value : '1080p';
                    window.__startBatchDownload(cleanTitle, selected, qual, 'MKV');
                });
            }
        }
    }

    // Toggle expand/collapse episode download links
    window.__toggleEpisodeLinks = async function (id, encodedUrl, epNum) {
        const detailsDiv = document.getElementById(`details-${id}`);
        const btn = document.getElementById(`btn-${id}`);
        if (!detailsDiv) return;

        if (!detailsDiv.hidden) {
            detailsDiv.hidden = true;
            if (btn) btn.innerHTML = '<span>⚡</span> Pilih Resolusi';
            return;
        }

        detailsDiv.hidden = false;
        if (btn) btn.innerHTML = '<span>✕</span> Tutup';

        // Jika sudah pernah di-load, tidak perlu fetch ulang
        if (detailsDiv.getAttribute('data-loaded') === 'true') {
            return;
        }

        const url = decodeURIComponent(encodedUrl);
        const provider = getProviderForUrl(url);

        try {
            const res = await fetch(`/api/${provider}/downloads?url=${encodeURIComponent(url)}`);
            const data = await res.json();

            if (!data.ok || !data.downloads || data.downloads.length === 0) {
                detailsDiv.innerHTML = `<span style="font-size:12px; color:#ef4444;">Tautan unduhan tidak ditemukan di halaman ini.</span>`;
                return;
            }

            detailsDiv.innerHTML = generateDownloadTiersHTML(data.downloads, epNum, id);
            detailsDiv.setAttribute('data-loaded', 'true');
        } catch (err) {
            detailsDiv.innerHTML = `<span style="font-size:12px; color:#ef4444;">Gagal memuat link: ${err.message}</span>`;
        }
    };

    // Helper untuk membuat HTML daftar pilihan resolusi per format (MKV, MP4)
    function generateDownloadTiersHTML(downloads, epNum, epId = 'ep') {
        if (!downloads || downloads.length === 0) {
            return '<div class="samehadaku-status-box" style="padding: 12px; font-size: 12px;">Tautan unduhan tidak tersedia.</div>';
        }

        // Kelompokkan per format (MKV, MP4, dll.)
        const formatGroups = {};
        downloads.forEach((tier, tIdx) => {
            const fmt = (tier.format || 'MKV').toUpperCase();
            if (!formatGroups[fmt]) formatGroups[fmt] = [];
            formatGroups[fmt].push({ tier, tIdx });
        });

        let html = `<div class="samehadaku-download-picker">`;

        Object.keys(formatGroups).forEach((fmt) => {
            const tiers = formatGroups[fmt];
            html += `
                <div class="samehadaku-format-row">
                    <span class="samehadaku-format-badge">${fmt}</span>
                    <div class="samehadaku-resolutions-grid">
            `;

            tiers.forEach(({ tier, tIdx }) => {
                const quality = tier.quality || '720p';
                const tierKey = `${epId}_${fmt}_${tIdx}_${quality}`.replace(/[^a-zA-Z0-9_-]/g, '_');

                // Daftarkan tier ke registry untuk aksi klik resolusi
                downloadTiersRegistry[tierKey] = {
                    epId: epId,
                    epNum: epNum,
                    format: fmt,
                    quality: quality,
                    links: tier.links || [],
                    animeName: currentAnimeName,
                    provider: currentProvider,
                };

                html += `
                    <button
                        type="button"
                        class="samehadaku-btn-res"
                        id="btn-res-${tierKey}"
                        title="Unduh langsung resolusi ${quality} (${fmt})"
                        onclick="window.__handleResolutionClick('${tierKey}')"
                    >
                        <span class="samehadaku-res-icon">⚡</span>
                        <span class="samehadaku-res-text">${quality}</span>
                    </button>
                `;
            });

            html += `
                    </div>
                </div>
            `;
        });

        // Kontainer untuk menampilkan mirror alternatif jika direct gagal atau tidak tersedia
        html += `
            <div class="samehadaku-fallback-container" id="fallback-${epId}" hidden></div>
        </div>`;

        return html;
    }

    // Klik tombol resolusi: otomatis unduh direct, jika gagal atau tidak tersedia maka tampilkan alternatif
    window.__handleResolutionClick = async function (tierKey) {
        const tier = downloadTiersRegistry[tierKey];
        if (!tier) return;

        const epId = tier.epId;
        const resBtn = document.getElementById(`btn-res-${tierKey}`);
        const fallbackContainer = document.getElementById(`fallback-${epId}`);
        const provider = tier.provider || currentProvider;

        // Tampilkan status loading pada tombol
        if (resBtn) {
            resBtn.classList.add('loading');
            resBtn.innerHTML = `<span class="samehadaku-spinner" style="width:12px; height:12px; margin:0; border-width:2px;"></span> <span>${tier.quality}</span>`;
        }

        // Cari direct download link (Prioritas 1: Pixeldrain, Prioritas 2: Mediafire direct resolver)
        let directUrl = null;
        const directLink = (tier.links || []).find(l => l.is_direct && (l.direct_download_url || l.direct_url));
        if (directLink) {
            directUrl = directLink.direct_download_url || directLink.direct_url;
        } else {
            // Hanya gunakan Mediafire jika URL-nya benar-benar domain mediafire.com dan bukan safelink/shortener
            const mfLink = (tier.links || []).find(l => {
                const u = (l.url || '').toLowerCase();
                const isShortener = l.is_safelink || u.includes('sfe.ad') || u.includes('ouo.io') || u.includes('urlwebsite.com') || u.includes('shorturl');
                return !isShortener && u.includes('mediafire.com');
            });
            if (mfLink && mfLink.url) {
                directUrl = mfLink.url;
            }
        }

        if (!directUrl) {
            // Direct download tidak tersedia di provider ini untuk resolusi ini
            if (resBtn) {
                resBtn.classList.remove('loading');
                resBtn.innerHTML = `<span class="samehadaku-res-icon">⚡</span> <span>${tier.quality}</span>`;
            }
            const hasSafelink = (tier.links || []).some(l => l.is_safelink || (l.url || '').includes('sfe.ad') || (l.url || '').includes('ouo.io') || (l.url || '').includes('urlwebsite.com'));
            const reasonMsg = hasSafelink
                ? 'Tautan unduhan Lendrive dilindungi safelink (sfe.ad). Silakan klik server di bawah untuk membuka dan melewati safelink di browser Anda:'
                : `Tautan unduh langsung tidak tersedia untuk ${tier.format} ${tier.quality}.`;
            showAlternativeServers(epId, tier, reasonMsg);
            return;
        }

        // Mulai unduhan langsung ke backend AniBase
        const ext = (tier.format || 'mkv').toLowerCase();
        const rawAnime = tier.animeName || currentAnimeName || 'Anime';
        const cleanAnime = rawAnime
            .replace(/(?:\s*-\s*)?(?:episode|episodes|eps?|e)\s*\d+.*$/i, '')
            .replace(/\s*[-–—]\s*\d{1,4}(?:\.\d+)?\s*$/i, '')
            .replace(/[\s\-–—]+$/, '')
            .trim() || 'Anime';
        const epPadded = tier.epNum ? String(tier.epNum).padStart(2, '0') : '01';
        const filename = provider === 'lendrive'
            ? `[LENDRIVE] ${cleanAnime} - ${epPadded} [${tier.quality} HEVC][DualSubs].${ext}`
            : `${cleanAnime} - Ep ${tier.epNum || '1'} [${tier.quality}].${ext}`;
        const token = getActionToken();

        const tray = document.getElementById('samehadakuTray');
        if (tray) tray.hidden = false;

        try {
            const res = await fetch(`/api/${provider}/download`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    url: directUrl,
                    anime_name: cleanAnime,
                    episode: tier.epNum,
                    filename: filename,
                    provider: provider,
                    action_token: token,
                })
            });

            let data;
            const rawText = await res.text();
            try {
                data = JSON.parse(rawText);
            } catch (jsonErr) {
                data = { ok: false, error: `Respon server (${res.status})` };
            }

            if (!data.ok) {
                // Direct download gagal dimulai di server
                if (resBtn) {
                    resBtn.classList.remove('loading');
                    resBtn.innerHTML = `<span class="samehadaku-res-icon">⚡</span> <span>${tier.quality}</span>`;
                }
                const errorMsg = data.message || data.error || 'Server unduh langsung tidak merespons';
                showAlternativeServers(epId, tier, `Unduh direct gagal (${errorMsg}).`);
                return;
            }

            // Direct download berhasil dimulai!
            if (resBtn) {
                resBtn.classList.remove('loading');
                resBtn.classList.add('success');
                resBtn.innerHTML = `<span>✓</span> <span>Mengunduh ${tier.quality}</span>`;
                setTimeout(() => {
                    if (resBtn) {
                        resBtn.classList.remove('success');
                        resBtn.innerHTML = `<span class="samehadaku-res-icon">⚡</span> <span>${tier.quality}</span>`;
                    }
                }, 4000);
            }

            // Sembunyikan fallback server jika sebelumnya terbuka
            if (fallbackContainer) {
                fallbackContainer.hidden = true;
            }

            // Jalankan polling tray status unduhan
            startStatusPolling();
            pollDownloadStatus();

        } catch (err) {
            if (resBtn) {
                resBtn.classList.remove('loading');
                resBtn.innerHTML = `<span class="samehadaku-res-icon">⚡</span> <span>${tier.quality}</span>`;
            }
            showAlternativeServers(epId, tier, `Koneksi unduh direct error: ${err.message}`);
        }
    };

    // Tampilkan pilihan tempat download lain saat direct tidak berhasil atau tidak tersedia
    function showAlternativeServers(epId, tier, reason) {
        const container = document.getElementById(`fallback-${epId}`);
        if (!container) return;

        const links = tier.links || [];
        const externalLinks = links.filter(l => !l.is_direct || (!l.direct_download_url && !l.direct_url));
        const listToDisplay = externalLinks.length > 0 ? externalLinks : links;

        let linksHTML = '';
        if (listToDisplay.length === 0) {
            linksHTML = '<span style="font-size:12px; color:var(--text-secondary, #A5B4D6);">Tidak ada mirror download lain yang tersedia untuk resolusi ini.</span>';
        } else {
            listToDisplay.forEach((l) => {
                const isSafelink = Boolean(l.is_safelink || (l.url || '').includes('sfe.ad') || (l.url || '').includes('ouo.io') || (l.url || '').includes('urlwebsite.com'));
                const btnTitle = isSafelink
                    ? `Buka tautan ${l.host} (Safelink) di browser`
                    : `Unduh lewat server ${l.host}`;
                const labelHtml = isSafelink
                    ? `<span>🔗 ${l.host} <small style="opacity:0.85; font-size:10px; font-weight:normal;">(Safelink)</small></span>`
                    : `<span>${l.host}</span>`;
                linksHTML += `
                    <a
                        href="${l.url}"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="samehadaku-btn-ext ${isSafelink ? 'samehadaku-btn-safelink' : ''}"
                        title="${btnTitle}"
                    >
                        ${labelHtml}
                        <span aria-hidden="true">&nearr;</span>
                    </a>
                `;
            });
        }

        const isSafelinkNotice = reason && (reason.toLowerCase().includes('safelink') || reason.toLowerCase().includes('sfe.ad'));
        const subText = isSafelinkNotice
            ? 'Silakan klik salah satu server di bawah untuk membuka dan mengunduh berkas melalui browser Anda:'
            : 'Silakan pilih tempat download alternatif di bawah ini untuk membuka tautan:';

        container.hidden = false;
        container.innerHTML = `
            <div class="samehadaku-fallback-box ${isSafelinkNotice ? 'safelink-warning' : ''}">
                <div class="samehadaku-fallback-header">
                    <div class="samehadaku-fallback-title-wrap">
                        <span class="samehadaku-fallback-badge">${tier.format} ${tier.quality}</span>
                        <span class="samehadaku-fallback-msg">${reason}</span>
                    </div>
                    <button type="button" class="samehadaku-fallback-close" onclick="document.getElementById('fallback-${epId}').hidden = true;" title="Tutup alternatif">✕</button>
                </div>
                <div class="samehadaku-fallback-sub">${subText}</div>
                <div class="samehadaku-fallback-links">
                    ${linksHTML}
                </div>
            </div>
        `;
    }

    // Memulai unduhan langsung (Pixeldrain) ke background worker
    window.__startDirectDownload = async function (encodedUrl, encodedEp, encodedFilename) {
        const url = decodeURIComponent(encodedUrl);
        const ep = decodeURIComponent(encodedEp);
        const filename = decodeURIComponent(encodedFilename || '');
        const token = getActionToken();
        const rawAnime = currentAnimeName || 'Anime';
        const cleanAnime = rawAnime
            .replace(/(?:\s*-\s*)?(?:episode|episodes|eps?|e)\s*\d+.*$/i, '')
            .replace(/\s*[-–—]\s*\d{1,4}(?:\.\d+)?\s*$/i, '')
            .replace(/[\s\-–—]+$/, '')
            .trim() || 'Anime';
        const provider = getProviderForUrl(url);
        const epPadded = ep ? String(ep).padStart(2, '0') : '01';
        const finalFilename = filename || (provider === 'lendrive'
            ? `[LENDRIVE] ${cleanAnime} - ${epPadded} [HEVC][DualSubs].mkv`
            : `${cleanAnime} - Ep ${ep || '1'}.mkv`);

        const tray = document.getElementById('samehadakuTray');
        if (tray) tray.hidden = false;

        try {
            const res = await fetch(`/api/${provider}/download`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    url: url,
                    anime_name: cleanAnime,
                    episode: ep,
                    filename: finalFilename,
                    provider: provider,
                    action_token: token,
                })
            });

            const data = await res.json();
            if (!data.ok) {
                alert(`Gagal memulai unduhan: ${data.message || data.error || 'Error'}`);
                return;
            }

            // Tampilkan notifikasi singkat
            startStatusPolling();
            pollDownloadStatus();
        } catch (err) {
            alert(`Error memulai unduhan: ${err.message}`);
        }
    };

    // Memulai antrean unduhan batch episode yang belum ada
    window.__startBatchDownload = async function (animeTitle, epsList, quality = '1080p', format = 'MKV') {
        const token = getActionToken();
        const tray = document.getElementById('samehadakuTray');
        if (tray) tray.hidden = false;

        const payloadEps = epsList.map(e => ({
            episode: String(e.episode || e._epNum || ''),
            url: e.url,
            title: e.title || '',
        }));

        const provider = (epsList.length > 0 && epsList[0].url && epsList[0].url.includes('lendrive.web.id')) ? 'lendrive' : currentProvider;

        try {
            const res = await fetch(`/api/${provider}/download/batch`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    anime_name: animeTitle,
                    episodes: payloadEps,
                    preferred_quality: quality,
                    preferred_format: format,
                    action_token: token,
                })
            });

            const data = await res.json();
            if (!data.ok) {
                alert(`Gagal memulai unduhan batch: ${data.message || data.error || 'Terjadi kesalahan'}`);
                return;
            }

            startStatusPolling();
            pollDownloadStatus();
        } catch (err) {
            alert(`Error memulai antrean batch: ${err.message}`);
        }
    };

    // Membatalkan seluruh antrean unduhan batch
    window.__cancelBatchDownload = async function (batchId) {
        if (!confirm('Apakah Anda yakin ingin membatalkan seluruh antrean batch episode ini?')) return;
        const token = getActionToken();
        try {
            const res = await fetch('/api/samehadaku/download/batch/cancel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    batch_id: batchId,
                    action_token: token,
                })
            });
            const data = await res.json();
            if (data.ok) {
                pollDownloadStatus();
            }
        } catch (err) {
            console.error('Failed to cancel batch:', err);
        }
    };

    // Fungsi helper untuk memperbarui badge episode di DOM jika episode selesai diunduh
    function updateEpisodeBadgeInDom(epNum) {
        if (!epNum) return;
        const targetNum = String(epNum).trim();
        const epItems = document.querySelectorAll('.samehadaku-ep-item');
        epItems.forEach(item => {
            const badgeNum = item.querySelector('.samehadaku-ep-badge-num');
            if (badgeNum && badgeNum.textContent.replace(/\D/g, '') === targetNum) {
                const titleRow = item.querySelector('.samehadaku-ep-title-row');
                if (titleRow) {
                    const missingBadge = titleRow.querySelector('.samehadaku-ep-badge-missing');
                    if (missingBadge) missingBadge.remove();

                    if (!titleRow.querySelector('.samehadaku-ep-badge-local')) {
                        const badge = document.createElement('span');
                        badge.className = 'samehadaku-ep-badge-local';
                        badge.title = 'Episode ini sudah ada di folder library Anda';
                        badge.textContent = '✓ Di Library';
                        titleRow.appendChild(badge);
                    }
                }
                const cbWrap = item.querySelector('.samehadaku-batch-checkbox-wrap');
                if (cbWrap) cbWrap.remove();
                item.classList.remove('is-missing');
            }
        });
    }

    // Polling status unduhan individu dan batch
    async function pollDownloadStatus() {
        try {
            const [tasksRes, batchRes] = await Promise.all([
                fetch('/api/samehadaku/download/status'),
                fetch('/api/samehadaku/download/batch/status')
            ]);
            const tasksData = await tasksRes.json();
            const batchData = await batchRes.json();

            const tasks = tasksData.ok ? (tasksData.tasks || []) : [];
            const batches = batchData.ok ? (batchData.batches || (batchData.batch ? [batchData.batch] : [])) : [];
            const queueInfo = tasksData.ok ? tasksData.queue_info : null;

            renderDownloadTasks(tasks, batches, queueInfo);

            // Jika ada unduhan yang selesai, tandai episode sebagai ada di library
            tasks.forEach(t => {
                if (t.status === 'completed' && t.episode) {
                    const epStr = String(t.episode).trim();
                    if (!localEpisodeNumbers.has(epStr)) {
                        localEpisodeNumbers.add(epStr);
                        updateEpisodeBadgeInDom(epStr);
                    }
                }
            });

            // Periksa apakah masih ada unduhan atau batch yang sedang aktif
            const anyActiveTask = tasks.some(t => t.status === 'downloading' || t.status === 'queued' || t.status === 'pending');
            const anyActiveBatch = batches.some(b => b.status === 'in_progress');
            if (!anyActiveTask && !anyActiveBatch && pollTimer) {
                // Semua selesai atau menganggur
            }
        } catch (err) {
            // Abaikan error polling transien
        }
    }

    function startStatusPolling() {
        if (pollTimer) return;
        pollDownloadStatus();
        pollTimer = setInterval(pollDownloadStatus, 1500);
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // Render daftar tugas unduhan dan antrean batch di drawer bawah
    function renderDownloadTasks(tasks, batches = [], queueInfo = null) {
        const tray = document.getElementById('samehadakuTray');
        const container = document.getElementById('samehadakuTasksList');
        if (!tray || !container) return;

        if (tasks.length === 0 && batches.length === 0) {
            tray.hidden = true;
            return;
        }

        tray.hidden = false;

        // Perbarui kontrol kuota paralel dan indikator antrean
        if (queueInfo) {
            const limitSelect = document.getElementById('samehadakuQueueLimitSelect');
            if (limitSelect && document.activeElement !== limitSelect && queueInfo.max_concurrent) {
                limitSelect.value = String(queueInfo.max_concurrent);
            }
            const indicator = document.getElementById('samehadakuQueueIndicator');
            if (indicator) {
                if (queueInfo.queued_count > 0 || queueInfo.paused_count > 0) {
                    indicator.textContent = `(${queueInfo.active_count}/${queueInfo.max_concurrent} aktif • ${queueInfo.queued_count} antre${queueInfo.paused_count > 0 ? ` • ${queueInfo.paused_count} jeda` : ''})`;
                } else if (queueInfo.active_count > 0) {
                    indicator.textContent = `(${queueInfo.active_count}/${queueInfo.max_concurrent} aktif)`;
                } else {
                    indicator.textContent = '';
                }
            }
        }

        let html = '';

        // 1. Tampilkan kartu progress Batch jika ada
        const activeOrRecentBatches = batches.slice(0, 3);
        activeOrRecentBatches.forEach(b => {
            const batchPercent = b.total_episodes > 0 ? Math.min(100, Math.round((b.completed_count / b.total_episodes) * 100)) : 0;
            const isCompleted = b.status === 'completed';
            const isCancelled = b.status === 'cancelled';
            const isInProgress = b.status === 'in_progress';

            let batchStatusText = '';
            if (isInProgress) {
                batchStatusText = `Sedang memproses <strong>Episode ${b.current_episode || '?'}</strong>...`;
            } else if (isCompleted) {
                batchStatusText = `<span style="color:var(--primary, #10b981); font-weight:700;">✓ Batch Selesai (${b.completed_count}/${b.total_episodes} Episode)</span>`;
            } else if (isCancelled) {
                batchStatusText = `<span style="color:var(--muted, #94a3b8);">Antrean Batch Dibatalkan</span>`;
            }

            html += `
                <div class="samehadaku-batch-tray-card ${b.status}" id="batch-${b.batch_id}">
                    <div class="samehadaku-batch-tray-top">
                        <div class="samehadaku-batch-tray-title-wrap">
                            <span class="samehadaku-batch-tray-icon">⚡</span>
                            <span class="samehadaku-batch-tray-title">Batch: ${b.anime_name} [${b.preferred_quality}]</span>
                        </div>
                        <span class="samehadaku-batch-tray-counts">
                            ${b.completed_count} dari ${b.total_episodes} Selesai ${b.failed_count > 0 ? `<span style="color:#ef4444; font-size:11px;">(${b.failed_count} gagal)</span>` : ''}
                        </span>
                    </div>
                    <div class="samehadaku-progress-bar-bg">
                        <div class="samehadaku-progress-bar-fill ${isCompleted ? 'completed' : (isCancelled ? 'error' : '')}" style="width: ${batchPercent}%;"></div>
                    </div>
                    <div class="samehadaku-batch-tray-bottom">
                        <span class="samehadaku-batch-tray-status">${batchStatusText}</span>
                        ${isInProgress ? `
                            <button type="button" class="samehadaku-batch-tray-cancel-btn" onclick="window.__cancelBatchDownload('${b.batch_id}')">Batalkan Batch</button>
                        ` : ''}
                    </div>
                </div>
            `;
        });

        // 2. Tampilkan tugas unduhan individu
        tasks.forEach(t => {
            const percent = t.total_bytes > 0 ? Math.min(100, (t.downloaded_bytes / t.total_bytes * 100)).toFixed(1) : (t.percent || 0);
            const statusClass = t.status === 'completed' ? 'completed' : (t.status === 'error' || t.status === 'cancelled' ? 'error' : (t.status === 'paused' ? 'paused' : ''));
            const speedText = t.status === 'downloading' ? formatSpeed(t.speed_bps) : '';
            const downloadedText = formatBytes(t.downloaded_bytes);
            const totalText = t.total_bytes > 0 ? formatBytes(t.total_bytes) : '?';
            const taskProvider = t.provider || 'samehadaku';
            const providerBadgeHtml = `<span class="samehadaku-task-provider-badge ${taskProvider}">${taskProvider === 'lendrive' ? '⚡ Lendrive' : '🐟 Samehadaku'}</span>`;

            let statusLabel = t.status;
            let destInfo = '';
            let actionButtons = '';

            if (t.status === 'completed') {
                statusLabel = '<span style="color:var(--primary, #10b981); font-weight:700;">✓ Selesai</span>';
                const folderName = t.dest_anime_folder || t.matched_anime;
                if (folderName) {
                    destInfo = ` &bull; Tersimpan di: <strong style="color:var(--cyan, #38bdf8);">${folderName}</strong>`;
                }
            } else if (t.status === 'downloading') {
                statusLabel = '<span style="color:var(--cyan, #38bdf8); font-weight:600;">⬇️ Mengunduh</span>';
                if (t.is_resumed) {
                    statusLabel += ' <span class="samehadaku-task-badge-resumed" title="Unduhan dilanjutkan dari byte sebelumnya">⚡ Dilanjutkan</span>';
                }
                const folderName = t.dest_anime_folder || t.matched_anime;
                if (folderName) {
                    destInfo = ` &bull; Tujuan: <em>${folderName}</em>`;
                }
                actionButtons = `
                    <button type="button" class="samehadaku-task-pause-btn" onclick="window.__pauseSamehadakuDownload('${t.task_id}')" title="Jeda unduhan ini">⏸️ Jeda</button>
                    <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelSamehadakuDownload('${t.task_id}')" title="Batalkan unduhan ini">Batalkan</button>
                `;
            } else if (t.status === 'queued') {
                const pos = t.queue_position ? `#${t.queue_position}` : '';
                statusLabel = `<span style="color:#f59e0b; font-weight:600;">⏳ Mengantre ${pos}</span>`;
                actionButtons = `
                    <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelSamehadakuDownload('${t.task_id}')" title="Keluarkan dari antrean">Batalkan</button>
                `;
            } else if (t.status === 'paused') {
                statusLabel = '<span style="color:#f97316; font-weight:600;">⏸️ Dijeda</span>';
                actionButtons = `
                    <button type="button" class="samehadaku-task-resume-btn" onclick="window.__resumeSamehadakuDownload('${t.task_id}')" title="Lanjutkan unduhan">▶️ Lanjut</button>
                    <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelSamehadakuDownload('${t.task_id}')" title="Batalkan unduhan">Batalkan</button>
                `;
            } else if (t.status === 'error') {
                statusLabel = '<span style="color:#ef4444; font-weight:700;">❌ Gagal</span>';
                actionButtons = `
                    <button type="button" class="samehadaku-task-retry-btn" onclick="window.__resumeSamehadakuDownload('${t.task_id}')" title="Coba lagi unduhan dari byte terakhir">🔄 Coba Lagi</button>
                    <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelSamehadakuDownload('${t.task_id}')" title="Hapus dari daftar">Hapus</button>
                `;
            } else if (t.status === 'cancelled') {
                statusLabel = '<span style="color:var(--muted, #94a3b8);">Dibatalkan</span>';
            }

            html += `
                <div class="samehadaku-task-item" id="task-${t.task_id}">
                    <div class="samehadaku-task-top">
                        <span class="samehadaku-task-filename" title="${t.filename}">${providerBadgeHtml}${t.filename}</span>
                        <span class="samehadaku-task-stats">${downloadedText} / ${totalText} (${percent}%) ${speedText ? `&bull; ${speedText}` : ''}</span>
                    </div>
                    <div class="samehadaku-progress-bar-bg">
                        <div class="samehadaku-progress-bar-fill ${statusClass}" style="width: ${percent}%;"></div>
                    </div>
                    <div class="samehadaku-task-bottom">
                        <span>Status: ${statusLabel}${destInfo} ${t.error ? ` - <span style="color:#ef4444;">${t.error}</span>` : ''}</span>
                        <div class="samehadaku-task-actions">
                            ${actionButtons}
                        </div>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    // Jeda unduhan (Pause)
    window.__pauseSamehadakuDownload = async function (taskId) {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/pause', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    task_id: taskId,
                    action_token: token,
                })
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to pause download:', err);
        }
    };

    // Lanjutkan / Coba Lagi unduhan (Resume / Retry)
    window.__resumeSamehadakuDownload = async function (taskId) {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/resume', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    task_id: taskId,
                    action_token: token,
                })
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to resume download:', err);
        }
    };

    // Ubah kuota download paralel
    window.__setDownloadQueueLimit = async function (limit) {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/queue/settings', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    max_concurrent: parseInt(limit, 10),
                    action_token: token,
                })
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to set download queue limit:', err);
        }
    };

    // Batalkan unduhan
    window.__cancelSamehadakuDownload = async function (taskId) {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/cancel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    task_id: taskId,
                    action_token: token,
                })
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to cancel download:', err);
        }
    };

    // Bersihkan tugas unduhan yang selesai
    async function clearCompletedTasks() {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/clear', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    action_token: token,
                })
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to clear completed tasks:', err);
        }
    }

    // Pencarian serial dari kartu Ongoing Tracker
    window.__searchFromTracker = function (encodedTitle) {
        const title = decodeURIComponent(encodedTitle);
        const searchInput = document.getElementById('samehadakuSearchInput');
        if (searchInput) searchInput.value = title;
        triggerSearch(title);
    };

    // Tracking notification ID yang sudah dimunculkan dalam sesi ini
    const shownToastNotificationIds = new Set();

    // Tampilkan Floating Glassmorphism Toast saat ada episode baru rilis
    function showOngoingEpisodeToast(notif) {
        if (!notif || !notif.id) return;
        if (shownToastNotificationIds.has(notif.id)) return;
        shownToastNotificationIds.add(notif.id);

        let toast = document.getElementById('samehadakuOngoingToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'samehadakuOngoingToast';
            toast.className = 'samehadaku-ongoing-toast';
            document.body.appendChild(toast);
        }

        const thumb = notif.thumbnail || '/static/images/placeholder.png';
        const title = notif.anime_name || 'Anime Ongoing';
        const ep = `Episode ${notif.episode_number} Baru Rilis!`;
        const meta = notif.released_on ? `Samehadaku · ${notif.released_on}` : 'Tersedia di Samehadaku';

        toast.innerHTML = `
            <div class="samehadaku-toast-header">
                <span class="samehadaku-toast-badge">✨ Episode Baru Rilis</span>
                <button type="button" class="samehadaku-toast-close" id="btnCloseOngoingToast" aria-label="Tutup">&times;</button>
            </div>
            <div class="samehadaku-toast-body">
                <img src="${thumb}" class="samehadaku-toast-thumb" alt="" onerror="this.src='/static/images/placeholder.png'">
                <div class="samehadaku-toast-info">
                    <div class="samehadaku-toast-title" title="${title}">${title}</div>
                    <div class="samehadaku-toast-ep">${ep}</div>
                    <div class="samehadaku-toast-meta">${meta}</div>
                </div>
            </div>
            <div class="samehadaku-toast-footer">
                <button type="button" class="samehadaku-toast-btn-dl" id="btnDlOngoingToast">
                    ⬇️ Unduh Sekarang
                </button>
                <button type="button" class="samehadaku-toast-btn-view" id="btnViewOngoingToast">
                    Buka Serial
                </button>
            </div>
        `;

        // Tampilkan toast
        requestAnimationFrame(() => {
            toast.classList.add('show');
        });

        let hideTimer = setTimeout(() => {
            toast.classList.remove('show');
        }, 12000);

        const closeBtn = toast.querySelector('#btnCloseOngoingToast');
        if (closeBtn) {
            closeBtn.onclick = () => {
                clearTimeout(hideTimer);
                toast.classList.remove('show');
                const token = getActionToken();
                fetch('/api/ongoing-tracker/notifications/read', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-AniBase-Action-Token': token },
                    body: JSON.stringify({ id: notif.id, action_token: token })
                }).catch(() => {});
            };
        }

        const dlBtn = toast.querySelector('#btnDlOngoingToast');
        if (dlBtn) {
            dlBtn.onclick = () => {
                clearTimeout(hideTimer);
                toast.classList.remove('show');
                window.__startOngoingQuickDownload(notif.anime_name, notif.episode_url, notif.episode_number);
            };
        }

        const viewBtn = toast.querySelector('#btnViewOngoingToast');
        if (viewBtn) {
            viewBtn.onclick = () => {
                clearTimeout(hideTimer);
                toast.classList.remove('show');
                openSamehadakuModal(notif.anime_name);
            };
        }
    }

    // 1-Klik Unduh Langsung Episode Baru
    window.__startOngoingQuickDownload = async function (encodedAnimeName, encodedEpUrl, epNum) {
        const animeName = decodeURIComponent(encodedAnimeName);
        const epUrl = decodeURIComponent(encodedEpUrl || '');
        const token = getActionToken();

        // Pastikan tray terlihat
        const tray = document.getElementById('samehadakuTray');
        if (tray) tray.hidden = false;

        try {
            const res = await fetch('/api/ongoing-tracker/quick-download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    anime_name: animeName,
                    episode_url: epUrl,
                    quality: '1080p',
                    format: 'MKV',
                    action_token: token,
                })
            });
            const data = await res.json();
            if (data && data.ok) {
                startStatusPolling();
                pollDownloadStatus();
                if (lastActiveView === 'tracker') {
                    loadOngoingTrackerView();
                }
            } else {
                openSamehadakuModal(animeName);
            }
        } catch (err) {
            console.error('Failed quick download:', err);
            openSamehadakuModal(animeName);
        }
    };

    // Hapus anime dari Ongoing Tracker
    window.__removeOngoingTracker = async function (encodedAnimeName) {
        const animeName = decodeURIComponent(encodedAnimeName);
        if (!confirm(`Hapus "${animeName}" dari daftar pantau Ongoing?`)) {
            return;
        }
        const token = getActionToken();
        try {
            const res = await fetch('/api/ongoing-tracker/delete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({ anime_name: animeName, action_token: token })
            });
            const data = await res.json();
            if (data && data.ok) {
                loadOngoingTrackerView();
            } else {
                alert(data.error || 'Gagal menghapus anime dari pantauan');
            }
        } catch (err) {
            console.error('Failed to remove ongoing tracker item:', err);
            alert('Terjadi kesalahan saat menghapus anime');
        }
    };

    // Pemeriksaan manual rilisan episode baru untuk modal tracker
    window.__checkOngoingReleases = async function () {
        const btn = document.getElementById('btnModalTrackerCheck');
        if (btn) btn.classList.add('loading');
        const token = getActionToken();

        try {
            const res = await fetch('/api/ongoing-tracker/check', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': token,
                    'X-Action-Token': token,
                },
                body: JSON.stringify({ deep: false, action_token: token })
            });
            const data = await res.json();
            if (data && data.ok) {
                if (data.new_alerts && data.new_alerts.length > 0) {
                    showOngoingEpisodeToast(data.new_alerts[0]);
                }
                loadOngoingTrackerView();
            }
        } catch (err) {
            console.error('Check ongoing error:', err);
        } finally {
            if (btn) btn.classList.remove('loading');
        }
    };

    // Pengecekan notifikasi episode baru yang belum dibaca (untuk popup toast)
    async function checkUnreadEpisodeToasts() {
        try {
            const res = await fetch('/api/ongoing-tracker/notifications?unread_only=true&limit=1');
            const data = await res.json();
            if (data && data.ok && data.notifications && data.notifications.length > 0) {
                const notif = data.notifications[0];
                if (!notif.is_downloaded && !shownToastNotificationIds.has(notif.id)) {
                    showOngoingEpisodeToast(notif);
                }
            }
        } catch (e) {
            // silent
        }
    }

    // Inisialisasi tombol toggle pantau ongoing pada halaman detail anime
    function initDetailTrackButtons() {
        document.querySelectorAll('[data-ongoing-tracker-toggle]').forEach(btn => {
            const animeName = btn.getAttribute('data-anime-name') || '';
            if (!animeName) return;

            // Periksa status pemantauan saat ini
            fetch('/api/ongoing-tracker/list')
                .then(r => r.json())
                .then(d => {
                    if (d && d.ok && Array.isArray(d.tracked_anime)) {
                        const isTracked = d.tracked_anime.some(item =>
                            item.anime_name.toLowerCase() === animeName.toLowerCase() && item.is_tracked
                        );
                        if (isTracked) {
                            btn.classList.add('is-tracked');
                            const label = btn.querySelector('.track-label');
                            if (label) label.textContent = '✓ Sedang Dipantau';
                            btn.title = 'Anime ini sedang aktif dipantau. Klik untuk berhenti memantau.';
                        }
                    }
                })
                .catch(() => {});

            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                const currentlyTracked = btn.classList.contains('is-tracked');
                const nextState = !currentlyTracked;
                const token = getActionToken();

                try {
                    const res = await fetch('/api/ongoing-tracker/toggle', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-AniBase-Action-Token': token,
                            'X-Action-Token': token,
                        },
                        body: JSON.stringify({
                            anime_name: animeName,
                            is_tracked: nextState,
                            action_token: token,
                        })
                    });
                    const data = await res.json();
                    if (data && data.ok) {
                        if (nextState) {
                            btn.classList.add('is-tracked');
                            const label = btn.querySelector('.track-label');
                            if (label) label.textContent = '✓ Sedang Dipantau';
                            btn.title = 'Anime ini sedang aktif dipantau. Klik untuk berhenti memantau.';
                        } else {
                            btn.classList.remove('is-tracked');
                            const label = btn.querySelector('.track-label');
                            if (label) label.textContent = 'Pantau Ongoing';
                            btn.title = 'Pantau rilis episode baru anime ini di Samehadaku.';
                        }
                    }
                } catch (err) {
                    console.error('Failed to toggle tracking:', err);
                }
            });
        });
    }

    // Expose ke global window
    window.openSamehadakuModal = openSamehadakuModal;
    window.closeSamehadakuModal = closeSamehadakuModal;
    window.showOngoingEpisodeToast = showOngoingEpisodeToast;

    // Pasang handler shortcut keyboard (Esc untuk menutup)
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('samehadakuModal');
            if (modal && !modal.hidden) {
                closeSamehadakuModal();
            }
        }
    });

    // Pasang trigger otomatis jika tombol [data-samehadaku-trigger] ada di halaman
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-samehadaku-trigger]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const animeName = btn.getAttribute('data-anime-title') || btn.getAttribute('data-anime-name') || '';
                const query = btn.getAttribute('data-search-query') || btn.getAttribute('data-query') || animeName;
                const provider = btn.getAttribute('data-provider') || 'samehadaku';
                openSamehadakuModal(animeName, query, provider);
            });
        });

        initDetailTrackButtons();

        // Jalankan pengecekan toast setelah delay singkat
        setTimeout(checkUnreadEpisodeToasts, 2500);
        setInterval(checkUnreadEpisodeToasts, 60000);
    });
})();

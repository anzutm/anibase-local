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
    let currentAnimeDetailsData = null;
    let pollTimer = null;
    let localEpisodeNumbers = new Set();

    // Dapatkan action token untuk otorisasi POST request
    function getActionToken() {
        const meta = document.querySelector('meta[name="action-token"]');
        if (meta && meta.content) return meta.content;
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
                        <span class="samehadaku-eyebrow">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2L1 21h22L12 2zm0 3.99L19.53 19H4.47L12 5.99zM11 10h2v4h-2zm0 6h2v2h-2z"/></svg>
                            Samehadaku Integration
                        </span>
                        <h2 id="samehadakuModalTitle">Download Anime Episodes</h2>
                        <p id="samehadakuModalSubtitle">Unduh episode langsung dari Samehadaku ke library AniBase</p>
                    </div>
                    <button type="button" class="samehadaku-close-btn" data-samehadaku-close aria-label="Tutup">&times;</button>
                </header>

                <div class="samehadaku-search-row">
                    <input type="search" id="samehadakuSearchInput" placeholder="Cari judul anime di Samehadaku..." autocomplete="off">
                    <button type="button" id="samehadakuSearchBtn">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                        <span>Cari</span>
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
                        <h4>Unduhan Aktif / Riwayat</h4>
                        <button type="button" class="samehadaku-tray-clear-btn" id="samehadakuClearTrayBtn">Bersihkan yang selesai</button>
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

    // Buka modal
    function openSamehadakuModal(animeTitle, initialQuery) {
        const modal = ensureModalDOM();
        modal.hidden = false;
        document.body.classList.add('samehadaku-modal-open');

        detectLocalEpisodes();

        currentAnimeName = animeTitle || '';
        const searchInput = modal.querySelector('#samehadakuSearchInput');
        const query = initialQuery || animeTitle || '';

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

    // Muat rilisan anime terbaru dari beranda Samehadaku
    async function loadLatestReleases(page = 1) {
        lastActiveView = 'latest';
        showLoading('Memuat rilisan episode anime terbaru dari Samehadaku...');

        try {
            const res = await fetch(`/api/samehadaku/latest?page=${page}`);
            const data = await res.json();

            if (!data.ok || !data.results || data.results.length === 0) {
                showMessage('Belum ada episode terbaru yang dapat dimuat dari Samehadaku saat ini. Silakan coba cari judul anime secara manual.');
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
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        let html = `
            <div class="samehadaku-section-header">
                <div class="samehadaku-section-title-wrap">
                    <span class="samehadaku-section-title">
                        <span>🔥</span> Rilisan Episode Terbaru
                    </span>
                    <span class="samehadaku-section-subtitle">
                        Episode anime baru rilis dari Samehadaku. Klik untuk memilih resolusi download atau melihat serial.
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

            html += `
                <div class="samehadaku-latest-card" data-idx="${idx}">
                    <div class="samehadaku-latest-thumb-wrap" onclick="window.__quickDownloadEpisode('${encodeURIComponent(ep.episode_url)}', '${encodeURIComponent(ep.title)}', '${epNum}')" title="Klik untuk unduh ${ep.title} ${epLabel}">
                        <img class="samehadaku-latest-thumb" src="${thumbUrl}" alt="${ep.title}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/static/arcana.jpg'">
                        <div class="samehadaku-latest-overlay"></div>
                        <span class="samehadaku-latest-ep-badge">${epLabel}</span>
                        ${timeLabel ? `<span class="samehadaku-latest-time-badge">${timeLabel}</span>` : ''}
                    </div>
                    <div class="samehadaku-latest-content">
                        <div class="samehadaku-latest-title" onclick="window.__quickDownloadEpisode('${encodeURIComponent(ep.episode_url)}', '${encodeURIComponent(ep.title)}', '${epNum}')" title="${ep.title}">
                            ${ep.title}
                        </div>
                        <div class="samehadaku-latest-actions">
                            <button type="button" class="samehadaku-btn-download-ep" onclick="window.__quickDownloadEpisode('${encodeURIComponent(ep.episode_url)}', '${encodeURIComponent(ep.title)}', '${epNum}')">
                                <span>⚡</span> Unduh
                            </button>
                            ${ep.anime_url ? `
                                <button type="button" class="samehadaku-btn-view-series" onclick="window.__viewAnimeSeries('${encodeURIComponent(ep.anime_url)}', '${encodeURIComponent(ep.title)}')" title="Lihat semua episode serial ini">
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

    // Unduh cepat episode tertentu langsung dari kartu latest releases
    window.__quickDownloadEpisode = async function (encodedEpUrl, encodedTitle, epNumber) {
        const epUrl = decodeURIComponent(encodedEpUrl);
        const title = decodeURIComponent(encodedTitle);
        currentAnimeName = title;
        lastActiveView = 'quick_ep';

        const displayEp = epNumber ? `Episode ${epNumber}` : 'Episode';
        showLoading(`Mengambil opsi resolusi unduhan untuk "${title}" ${displayEp}...`);

        try {
            const res = await fetch(`/api/samehadaku/downloads?url=${encodeURIComponent(epUrl)}`);
            const data = await res.json();

            if (!data.ok || !data.downloads || data.downloads.length === 0) {
                showMessage(`Tidak ditemukan tautan unduhan untuk episode ini pada Samehadaku. Silakan coba cari judul serialnya secara manual.`, true);
                return;
            }

            renderQuickEpisodeDownloads(epUrl, title, epNumber, data.downloads);
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
                <div>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()">&larr; Kembali ke Rilisan Terbaru</button>
                    <h3 style="margin-top: 6px;">${title} &bull; ${epDisplay}</h3>
                </div>
                <div style="font-size: 12px; color: var(--text-secondary, #A5B4D6);">
                    Pilih format & resolusi di bawah
                </div>
            </div>
            <div class="samehadaku-ep-item" style="border-color: color-mix(in srgb, var(--primary) 50%, transparent); box-shadow: 0 4px 20px var(--card-glow);">
                <div class="samehadaku-ep-details" style="display:block;">
        `;

        html += generateDownloadTiersHTML(downloads, epNumber || '1');

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
        if (lastActiveView === 'search' && currentSeriesResults.length > 0) {
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
        showLoading(`Mencari "${query}" di Samehadaku...`);

        try {
            const res = await fetch(`/api/samehadaku/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();

            if (!data.ok || !data.results || data.results.length === 0) {
                showMessage(`
                    Tidak ditemukan hasil untuk "<strong>${query}</strong>" di Samehadaku. Coba kata kunci yang lebih singkat.<br><br>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()">&larr; Kembali ke Rilisan Terbaru</button>
                `);
                return;
            }

            currentSeriesResults = data.results;

            // Jika hanya ada 1 hasil yang merupakan anime hub, langsung buka detailnya
            const animeHubs = data.results.filter(r => r.is_anime);
            if (animeHubs.length === 1 && !data.results[0].is_episode) {
                loadAnimeDetails(animeHubs[0].url, animeHubs[0].title);
            } else {
                renderSearchResults(data.results);
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

        let html = `
            <div class="samehadaku-section-header">
                <div>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()">&larr; Kembali ke Rilisan Terbaru</button>
                    <div style="margin-top: 4px; font-size: 13px; color: var(--text-secondary, #A5B4D6);">
                        Ditemukan ${results.length} hasil di Samehadaku. Pilih serial anime yang sesuai:
                    </div>
                </div>
            </div>
            <div class="samehadaku-series-list">
        `;

        results.forEach((item, idx) => {
            const typeLabel = item.is_batch ? 'Batch' : (item.type || (item.is_anime ? 'Anime' : 'Episode'));
            const thumb = item.thumbnail || '';
            html += `
                <div class="samehadaku-series-card" data-idx="${idx}" onclick="window.__selectSamehadakuSeries(${idx})">
                    ${thumb ? `<img src="${thumb}" class="samehadaku-series-thumb" alt="${item.title}" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">` : ''}
                    <div class="samehadaku-series-title">${item.title}</div>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span class="samehadaku-series-badge">${typeLabel}</span>
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

        try {
            const res = await fetch(`/api/samehadaku/episodes?url=${encodeURIComponent(pageUrl)}`);
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

        let html = `
            <div class="samehadaku-episodes-header">
                <div>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToPreviousView()">&larr; Kembali</button>
                    <h3 style="margin-top: 4px;">${details.title || currentAnimeName}</h3>
                </div>
                <div style="font-size: 12px; color: var(--text-secondary, #A5B4D6);">
                    ${episodes.length} Episode tersedia
                </div>
            </div>
        `;

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
                            <div class="samehadaku-ep-info">
                                <span class="samehadaku-ep-number" style="width:auto; padding:0 8px; font-size:11px;">BATCH</span>
                                <span class="samehadaku-ep-title">${b.title}</span>
                            </div>
                            <button type="button" class="samehadaku-ep-expand-btn" id="btn-batch-${bIdx}">Tampilkan Link</button>
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

        // Render setiap episode
        episodes.forEach((ep, epIdx) => {
            const epNum = ep.episode || String(epIdx + 1);
            const isLocal = localEpisodeNumbers.has(String(epNum));

            html += `
                <div class="samehadaku-ep-item" id="item-ep-${epIdx}">
                    <div class="samehadaku-ep-summary" onclick="window.__toggleEpisodeLinks('ep-${epIdx}', '${encodeURIComponent(ep.url)}', '${epNum}')">
                        <div class="samehadaku-ep-info">
                            <span class="samehadaku-ep-number">${epNum}</span>
                            <span class="samehadaku-ep-title">${ep.title}</span>
                            ${isLocal ? `<span class="samehadaku-ep-badge-local" title="Episode ini sudah ada di folder library Anda">✓ Local</span>` : ''}
                        </div>
                        ${ep.date ? `<span class="samehadaku-ep-date">${ep.date}</span>` : ''}
                        <button type="button" class="samehadaku-ep-expand-btn" id="btn-ep-${epIdx}">Pilih Resolusi</button>
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
    }

    // Toggle expand/collapse episode download links
    window.__toggleEpisodeLinks = async function (id, encodedUrl, epNum) {
        const detailsDiv = document.getElementById(`details-${id}`);
        const btn = document.getElementById(`btn-${id}`);
        if (!detailsDiv) return;

        if (!detailsDiv.hidden) {
            detailsDiv.hidden = true;
            if (btn) btn.textContent = 'Pilih Resolusi';
            return;
        }

        detailsDiv.hidden = false;
        if (btn) btn.textContent = 'Tutup';

        // Jika sudah pernah di-load, tidak perlu fetch ulang
        if (detailsDiv.getAttribute('data-loaded') === 'true') {
            return;
        }

        const url = decodeURIComponent(encodedUrl);
        try {
            const res = await fetch(`/api/samehadaku/downloads?url=${encodeURIComponent(url)}`);
            const data = await res.json();

            if (!data.ok || !data.downloads || data.downloads.length === 0) {
                detailsDiv.innerHTML = `<span style="font-size:12px; color:#ef4444;">Tautan unduhan tidak ditemukan di halaman ini.</span>`;
                return;
            }

            detailsDiv.innerHTML = generateDownloadTiersHTML(data.downloads, epNum);
            detailsDiv.setAttribute('data-loaded', 'true');
        } catch (err) {
            detailsDiv.innerHTML = `<span style="font-size:12px; color:#ef4444;">Gagal memuat link: ${err.message}</span>`;
        }
    };

    // Helper untuk membuat HTML daftar tier dan link unduhan
    function generateDownloadTiersHTML(downloads, epNum) {
        let html = '';

        downloads.forEach((tier) => {
            const fmt = tier.format || 'MKV';
            const quality = tier.quality || '720p';
            const links = tier.links || [];

            html += `
                <div class="samehadaku-tier-group">
                    <div class="samehadaku-tier-header">
                        <span class="samehadaku-tier-format">${fmt}</span>
                        <span class="samehadaku-tier-quality">${quality}</span>
                    </div>
                    <div class="samehadaku-links-row">
            `;

            links.forEach((l) => {
                const directUrl = l.direct_download_url || l.direct_url;
                if (l.is_direct && directUrl) {
                    // Host direct (seperti Pixeldrain) dengan tombol unduh langsung ke library
                    const defaultName = `${currentAnimeName || 'Anime'} - Ep ${epNum || '1'} [${quality}].mkv`;
                    html += `
                        <button
                            type="button"
                            class="samehadaku-btn-direct"
                            title="Unduh langsung berkas ke folder library Anda"
                            onclick="window.__startDirectDownload('${encodeURIComponent(directUrl)}', '${encodeURIComponent(epNum || '')}', '${encodeURIComponent(defaultName)}')"
                        >
                            <span aria-hidden="true">⚡</span>
                            <span>Unduh ${l.host} (Direct)</span>
                        </button>
                    `;
                } else {
                    // Host eksternal (Gofile, Krakenfiles, Mediafire, dll.)
                    html += `
                        <a
                            href="${l.url}"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="samehadaku-btn-ext"
                            title="Buka tautan ${l.host} di tab baru"
                        >
                            <span>${l.host}</span>
                            <span aria-hidden="true">&nearr;</span>
                        </a>
                    `;
                }
            });

            html += `
                    </div>
                </div>
            `;
        });

        return html;
    }

    // Memulai unduhan langsung (Pixeldrain) ke background worker
    window.__startDirectDownload = async function (encodedUrl, encodedEp, encodedFilename) {
        const url = decodeURIComponent(encodedUrl);
        const ep = decodeURIComponent(encodedEp);
        const filename = decodeURIComponent(encodedFilename);
        const token = getActionToken();

        const tray = document.getElementById('samehadakuTray');
        if (tray) tray.hidden = false;

        try {
            const res = await fetch('/api/samehadaku/download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Action-Token': token,
                },
                body: JSON.stringify({
                    url: url,
                    anime_name: currentAnimeName,
                    episode: ep,
                    filename: filename,
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

    // Polling status unduhan
    async function pollDownloadStatus() {
        try {
            const res = await fetch('/api/samehadaku/download/status');
            const data = await res.json();
            if (!data.ok || !data.tasks) return;

            renderDownloadTasks(data.tasks);

            // Jika tidak ada unduhan yang sedang berjalan, hentikan polling cepat
            const anyActive = data.tasks.some(t => t.status === 'downloading' || t.status === 'pending');
            if (!anyActive && pollTimer) {
                // Biarkan tray terbuka tapi hentikan polling aktif
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

    // Render daftar tugas unduhan di drawer bawah
    function renderDownloadTasks(tasks) {
        const tray = document.getElementById('samehadakuTray');
        const container = document.getElementById('samehadakuTasksList');
        if (!tray || !container) return;

        if (tasks.length === 0) {
            tray.hidden = true;
            return;
        }

        tray.hidden = false;
        let html = '';

        tasks.forEach(t => {
            const percent = t.total_bytes > 0 ? Math.min(100, (t.downloaded_bytes / t.total_bytes * 100)).toFixed(1) : (t.percent || 0);
            const statusClass = t.status === 'completed' ? 'completed' : (t.status === 'error' || t.status === 'cancelled' ? 'error' : '');
            const speedText = t.status === 'downloading' ? formatSpeed(t.speed_bps) : '';
            const downloadedText = formatBytes(t.downloaded_bytes);
            const totalText = t.total_bytes > 0 ? formatBytes(t.total_bytes) : '?';

            html += `
                <div class="samehadaku-task-item" id="task-${t.task_id}">
                    <div class="samehadaku-task-top">
                        <span class="samehadaku-task-filename" title="${t.filename}">${t.filename}</span>
                        <span class="samehadaku-task-stats">${downloadedText} / ${totalText} (${percent}%) ${speedText ? `&bull; ${speedText}` : ''}</span>
                    </div>
                    <div class="samehadaku-progress-bar-bg">
                        <div class="samehadaku-progress-bar-fill ${statusClass}" style="width: ${percent}%;"></div>
                    </div>
                    <div class="samehadaku-task-bottom">
                        <span>Status: <strong>${t.status}</strong> ${t.error ? ` - ${t.error}` : ''}</span>
                        ${(t.status === 'downloading' || t.status === 'pending') ? `
                            <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelSamehadakuDownload('${t.task_id}')">Batalkan</button>
                        ` : ''}
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    // Batalkan unduhan
    window.__cancelSamehadakuDownload = async function (taskId) {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/cancel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Action-Token': token,
                },
                body: JSON.stringify({ task_id: taskId })
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
                    'X-Action-Token': token,
                },
                body: JSON.stringify({})
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to clear completed tasks:', err);
        }
    }

    // Expose ke global window
    window.openSamehadakuModal = openSamehadakuModal;
    window.closeSamehadakuModal = closeSamehadakuModal;

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
                const animeName = btn.getAttribute('data-anime-name') || '';
                const query = btn.getAttribute('data-search-query') || animeName;
                openSamehadakuModal(animeName, query);
            });
        });
    });
})();

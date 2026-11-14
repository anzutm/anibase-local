/**
 * static/samehadaku-downloader.js
 * Frontend logic for Samehadaku episode downloader in AniBase.
 */

(function () {
    'use strict';

    let currentAnimeName = '';
    let currentAnimeHubUrl = '';
    let currentSeriesResults = [];
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
                        <span class="samehadaku-eyebrow">Samehadaku Integration</span>
                        <h2 id="samehadakuModalTitle">Download Anime Episodes</h2>
                        <p id="samehadakuModalSubtitle">Unduh episode langsung dari Samehadaku ke library AniBase</p>
                    </div>
                    <button type="button" class="samehadaku-close-btn" data-samehadaku-close aria-label="Tutup">&times;</button>
                </header>

                <div class="samehadaku-search-row">
                    <input type="search" id="samehadakuSearchInput" placeholder="Cari judul anime di Samehadaku..." autocomplete="off">
                    <button type="button" id="samehadakuSearchBtn">Cari</button>
                </div>

                <div class="samehadaku-body" id="samehadakuModalBody">
                    <div class="samehadaku-status-box" id="samehadakuStatusBox">
                        Masukkan kata kunci untuk mencari episode di Samehadaku.
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
            }
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

    // Pencarian ke server AniBase
    async function triggerSearch(query) {
        if (!query) {
            showMessage('Masukkan judul anime untuk mencari.');
            return;
        }

        showLoading(`Mencari "${query}" di Samehadaku...`);

        try {
            const res = await fetch(`/api/samehadaku/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();

            if (!data.ok || !data.results || data.results.length === 0) {
                showMessage(`Tidak ditemukan hasil untuk "<strong>${query}</strong>" di Samehadaku. Coba kata kunci yang lebih singkat.`);
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
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        let html = `
            <div style="margin-bottom: 12px; font-size: 13px; color: #94a3b8;">
                Ditemukan ${results.length} hasil di Samehadaku. Pilih serial anime yang sesuai:
            </div>
            <div class="samehadaku-series-list">
        `;

        results.forEach((item, idx) => {
            const typeLabel = item.is_batch ? 'Batch' : (item.type || (item.is_anime ? 'Anime' : 'Episode'));
            const thumb = item.thumbnail || '';
            html += `
                <div class="samehadaku-series-card" data-idx="${idx}" onclick="window.__selectSamehadakuSeries(${idx})">
                    ${thumb ? `<img src="${thumb}" class="samehadaku-series-thumb" alt="${item.title}" loading="lazy" onerror="this.style.display='none'">` : ''}
                    <div class="samehadaku-series-title">${item.title}</div>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span class="samehadaku-series-badge">${typeLabel}</span>
                        ${item.score ? `<span style="font-size:11px; color:#fbbf24;">★ ${item.score}</span>` : ''}
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
        showLoading(`Mengambil daftar episode untuk "${title}"...`);
        currentAnimeHubUrl = pageUrl;

        try {
            const res = await fetch(`/api/samehadaku/episodes?url=${encodeURIComponent(pageUrl)}`);
            const data = await res.json();

            if (!data.ok) {
                showMessage(`Gagal memuat detail anime: ${data.error || 'Terjadi kesalahan'}`, true);
                return;
            }

            renderAnimeEpisodes(data);
        } catch (err) {
            showMessage(`Gagal memuat episode: ${err.message}`, true);
        }
    }

    // Render daftar episode
    function renderAnimeEpisodes(details) {
        const body = document.getElementById('samehadakuModalBody');
        if (!body) return;

        const episodes = details.episodes || [];
        const batchLinks = details.batch_links || [];

        let html = `
            <div class="samehadaku-episodes-header">
                <div>
                    <button type="button" class="samehadaku-back-to-series" onclick="window.__backToSamehadakuSeries()">&larr; Kembali ke hasil pencarian</button>
                    <h3 style="margin-top: 4px;">${details.title || currentAnimeName}</h3>
                </div>
                <div style="font-size: 12px; color: #94a3b8;">
                    ${episodes.length} Episode tersedia
                </div>
            </div>
        `;

        // Tampilkan batch links jika ada
        if (batchLinks.length > 0) {
            html += `
                <div style="margin-bottom: 16px; padding: 12px; border-radius: 12px; background: rgba(0, 183, 224, 0.08); border: 1px solid rgba(0, 183, 224, 0.25);">
                    <div style="font-size: 12px; font-weight: 700; color: #00b7e0; margin-bottom: 8px;">📦 BATCH DOWNLOAD TERSEDIA</div>
            `;
            batchLinks.forEach((b, bIdx) => {
                html += `
                    <div class="samehadaku-ep-item" style="margin-bottom: 6px;">
                        <div class="samehadaku-ep-summary" onclick="window.__toggleEpisodeLinks('batch-${bIdx}', '${encodeURIComponent(b.url)}')">
                            <div class="samehadaku-ep-info">
                                <span class="samehadaku-ep-number" style="width:auto; padding:0 8px;">BATCH</span>
                                <span class="samehadaku-ep-title">${b.title}</span>
                            </div>
                            <button type="button" class="samehadaku-ep-expand-btn" id="btn-batch-${bIdx}">Tampilkan Link</button>
                        </div>
                        <div class="samehadaku-ep-details" id="details-batch-${bIdx}" hidden>
                            <div class="samehadaku-spinner"></div> Memuat tautan unduhan...
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

    // Kembali ke hasil pencarian
    window.__backToSamehadakuSeries = function () {
        if (currentSeriesResults.length > 0) {
            renderSearchResults(currentSeriesResults);
        } else {
            const searchInput = document.getElementById('samehadakuSearchInput');
            triggerSearch(searchInput ? searchInput.value.trim() : currentAnimeName);
        }
    };

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

            renderDownloadTiers(detailsDiv, data.downloads, epNum);
            detailsDiv.setAttribute('data-loaded', 'true');
        } catch (err) {
            detailsDiv.innerHTML = `<span style="font-size:12px; color:#ef4444;">Gagal memuat link: ${err.message}</span>`;
        }
    };

    // Render daftar tier dan link download
    function renderDownloadTiers(container, downloads, epNum) {
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
                if (l.is_direct && l.direct_url) {
                    // Host direct (seperti Pixeldrain) dengan tombol unduh langsung ke library
                    const defaultName = `${currentAnimeName || 'Anime'} - Ep ${epNum || '1'} [${quality}].mkv`;
                    html += `
                        <button
                            type="button"
                            class="samehadaku-btn-direct"
                            title="Unduh langsung berkas ke folder library Anda"
                            onclick="window.__startDirectDownload('${encodeURIComponent(l.direct_url)}', '${encodeURIComponent(epNum || '')}', '${encodeURIComponent(defaultName)}')"
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

        container.innerHTML = html;
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
                // Turunkan frekuensi atau matikan
            }
        } catch (err) {
            // Silently ignore polling error
        }
    }

    // Render daftar task di drawer bawah modal
    function renderDownloadTasks(tasks) {
        const tray = document.getElementById('samehadakuTray');
        const list = document.getElementById('samehadakuTasksList');
        if (!tray || !list) return;

        if (tasks.length === 0) {
            tray.hidden = true;
            return;
        }

        tray.hidden = false;
        let html = '';

        tasks.forEach((t) => {
            const isCompleted = t.status === 'completed';
            const isError = t.status === 'error';
            const isCancelled = t.status === 'cancelled';
            const isDownloading = t.status === 'downloading';

            let statusLabel = 'Mengunduh';
            let fillClass = '';
            if (isCompleted) {
                statusLabel = 'Selesai ✓';
                fillClass = 'completed';
            } else if (isError) {
                statusLabel = `Gagal (${t.error || 'Error'})`;
                fillClass = 'error';
            } else if (isCancelled) {
                statusLabel = 'Dibatalkan';
                fillClass = 'error';
            }

            const percent = t.progress_percent || (isCompleted ? 100 : 0);
            const downloadedStr = formatBytes(t.downloaded_bytes);
            const totalStr = t.total_bytes > 0 ? formatBytes(t.total_bytes) : '?';
            const speedStr = isDownloading ? formatSpeed(t.speed_bps) : '';

            html += `
                <div class="samehadaku-task-item" id="task-${t.task_id}">
                    <div class="samehadaku-task-top">
                        <span class="samehadaku-task-filename" title="${t.filename}">${t.filename}</span>
                        <span class="samehadaku-task-stats">
                            ${downloadedStr} / ${totalStr} (${percent}%)
                            ${speedStr ? ` &bull; ${speedStr}` : ''}
                        </span>
                    </div>
                    <div class="samehadaku-progress-bar-bg">
                        <div class="samehadaku-progress-bar-fill ${fillClass}" style="width: ${percent}%;"></div>
                    </div>
                    <div class="samehadaku-task-bottom">
                        <span style="${isCompleted ? 'color:#22c55e;' : (isError || isCancelled ? 'color:#ef4444;' : 'color:#00b7e0;')}">
                            ${statusLabel}
                        </span>
                        ${isDownloading ? `
                            <button type="button" class="samehadaku-task-cancel-btn" onclick="window.__cancelDownload('${t.task_id}')">
                                Batal
                            </button>
                        ` : ''}
                    </div>
                </div>
            `;
        });

        list.innerHTML = html;
    }

    // Batalkan unduhan
    window.__cancelDownload = async function (taskId) {
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

    // Bersihkan task yang selesai
    async function clearCompletedTasks() {
        const token = getActionToken();
        try {
            await fetch('/api/samehadaku/download/clear', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Action-Token': token,
                }
            });
            pollDownloadStatus();
        } catch (err) {
            console.error('Failed to clear completed tasks:', err);
        }
    }

    function startStatusPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollDownloadStatus();
        pollTimer = setInterval(pollDownloadStatus, 1500);
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // Expose ke global window
    window.openSamehadakuDownloader = openSamehadakuModal;
    window.closeSamehadakuDownloader = closeSamehadakuModal;

    // Pasang auto-attach ke tombol-tombol yang memicu Samehadaku
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-samehadaku-trigger]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const animeTitle = btn.getAttribute('data-anime-title') || '';
                const query = btn.getAttribute('data-query') || animeTitle;
                openSamehadakuModal(animeTitle, query);
            });
        });
    });
})();

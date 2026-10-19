// static/spotlight.js - Global Spotlight Command Palette (Ctrl+K)
(() => {
    let overlay = null;
    let input = null;
    let resultsContainer = null;
    let clearBtn = null;
    let escBtn = null;

    let isOpen = false;
    let isInitialized = false;
    let currentQuery = '';
    let selectedIndex = 0;
    let visibleItems = [];
    let debounceTimer = null;
    let fetchController = null;
    const queryCache = new Map();

    const ACTION_ICONS = {
        home: `<svg viewBox="0 0 24 24"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>`,
        film: `<svg viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line><line x1="2" y1="7" x2="7" y2="7"></line><line x1="2" y1="17" x2="7" y2="17"></line><line x1="17" y1="17" x2="22" y2="17"></line><line x1="17" y1="7" x2="22" y2="7"></line></svg>`,
        calendar: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`,
        gear: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>`,
        user: `<svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>`,
        refresh: `<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>`
    };

    function initSpotlightDOM() {
        if (isInitialized && overlay) return true;

        overlay = document.getElementById('spotlightOverlay');
        if (!overlay) return false;

        input = document.getElementById('spotlightInput');
        resultsContainer = document.getElementById('spotlightResults');
        clearBtn = document.getElementById('spotlightClearBtn');
        escBtn = document.getElementById('spotlightEscBtn');

        const backdrop = document.getElementById('spotlightBackdrop');
        backdrop?.addEventListener('click', closeSpotlight);
        escBtn?.addEventListener('click', closeSpotlight);

        clearBtn?.addEventListener('click', () => {
            if (!input) return;
            input.value = '';
            input.focus();
            handleInput();
        });

        input?.addEventListener('input', handleInput);
        input?.addEventListener('keydown', handleKeyNavigation);

        // Hook up topbar search box
        hookTopbarSearch();
        isInitialized = true;
        return true;
    }

    function hookTopbarSearch() {
        const topbarSearch = document.getElementById('animeSearch');
        if (!topbarSearch) return;

        const searchBox = topbarSearch.closest('.search-box');
        if (searchBox && !searchBox.querySelector('.search-box-shortcut')) {
            const isMac = navigator.platform?.toUpperCase().includes('MAC');
            const shortcutBadge = document.createElement('kbd');
            shortcutBadge.className = 'search-box-shortcut';
            shortcutBadge.textContent = isMac ? '⌘K' : 'Ctrl K';
            searchBox.appendChild(shortcutBadge);
        }

        topbarSearch.addEventListener('click', (e) => {
            e.preventDefault();
            openSpotlight(topbarSearch.value);
        });

        topbarSearch.addEventListener('focus', (e) => {
            e.preventDefault();
            topbarSearch.blur();
            openSpotlight();
        });
    }

    function openSpotlight(initialQuery = '') {
        if (!overlay && !initSpotlightDOM()) return;

        isOpen = true;
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';

        // Trigger reflow for transition
        void overlay.offsetWidth;
        overlay.classList.add('is-open');

        const queryToSearch = (typeof initialQuery === 'string') ? initialQuery : '';
        if (input) {
            input.value = queryToSearch;
            input.focus();
            input.select();
        }

        if (clearBtn) clearBtn.hidden = !queryToSearch.trim();

        fetchSpotlightData(queryToSearch.trim());
    }

    function closeSpotlight() {
        if (!isOpen || !overlay) return;

        isOpen = false;
        overlay.classList.remove('is-open');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';

        if (fetchController) {
            fetchController.abort();
            fetchController = null;
        }

        setTimeout(() => {
            if (!isOpen && overlay) {
                overlay.hidden = true;
            }
        }, 220);
    }

    function toggleSpotlight() {
        if (isOpen) {
            closeSpotlight();
        } else {
            openSpotlight();
        }
    }

    function handleInput() {
        const query = (input?.value || '').trim();
        if (clearBtn) clearBtn.hidden = !query;

        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            fetchSpotlightData(query);
        }, 120);
    }

    function renderLoading(query = '') {
        if (!resultsContainer) return;
        const msg = query ? `Searching for "${escapeHtml(query)}"...` : 'Loading recommendations...';
        resultsContainer.innerHTML = `
            <div class="spotlight-empty-state">
                <div class="spotlight-spinner" role="status" aria-label="Loading"></div>
                <p class="spotlight-empty-text">${msg}</p>
            </div>
        `;
    }

    function renderEmpty(query = '') {
        if (!resultsContainer) return;
        const msg = query
            ? `No results found for "${escapeHtml(query)}"`
            : 'Type to search your local library...';
        resultsContainer.innerHTML = `
            <div class="spotlight-empty-state">
                <p class="spotlight-empty-text">${msg}</p>
            </div>
        `;
    }

    function renderError(message = 'Unable to search library.') {
        if (!resultsContainer) return;
        resultsContainer.innerHTML = `
            <div class="spotlight-empty-state">
                <p class="spotlight-empty-text" style="color: #f87171;">${escapeHtml(message)}</p>
            </div>
        `;
    }

    async function fetchSpotlightData(query) {
        currentQuery = query;
        const normalizedQuery = (query || '').trim();
        const cacheKey = normalizedQuery.toLowerCase();

        if (queryCache.has(cacheKey)) {
            renderResults(queryCache.get(cacheKey), normalizedQuery);
            return;
        }

        renderLoading(normalizedQuery);

        if (fetchController) {
            fetchController.abort();
        }
        fetchController = new AbortController();

        try {
            const response = await fetch(`/api/search/spotlight?q=${encodeURIComponent(normalizedQuery)}&limit=15`, {
                signal: fetchController.signal,
                headers: { 'Accept': 'application/json' }
            });
            if (!response.ok) throw new Error('Search request failed');
            const data = await response.json();
            queryCache.set(cacheKey, data);
            if (currentQuery === query) {
                renderResults(data, normalizedQuery);
            }
        } catch (err) {
            if (err.name !== 'AbortError') {
                if (currentQuery === query) {
                    renderError('Unable to search library.');
                }
            }
        }
    }

    function renderResults(data, query) {
        if (!resultsContainer) return;

        const results = data?.results || {};
        const animeList = results.anime || [];
        const movieList = results.movies || [];
        const actionList = results.actions || [];

        visibleItems = [];
        resultsContainer.innerHTML = '';

        const totalFound = animeList.length + movieList.length + actionList.length;

        if (totalFound === 0) {
            renderEmpty(query);
            return;
        }

        // 1. Anime Series Section
        if (animeList.length > 0) {
            const section = document.createElement('div');
            section.className = 'spotlight-section';
            section.innerHTML = `
                <div class="spotlight-section-title">
                    <span>Anime Series</span>
                    <span class="spotlight-section-count">${animeList.length}</span>
                </div>
            `;
            animeList.forEach((item) => {
                const element = createAnimeItemElement(item);
                section.appendChild(element);
                const itemIndex = visibleItems.length;
                element.addEventListener('mouseenter', () => {
                    selectedIndex = itemIndex;
                    highlightSelectedItem();
                });
                visibleItems.push({ element, data: item });
            });
            resultsContainer.appendChild(section);
        }

        // 2. Movies Section
        if (movieList.length > 0) {
            const section = document.createElement('div');
            section.className = 'spotlight-section';
            section.innerHTML = `
                <div class="spotlight-section-title">
                    <span>Movies</span>
                    <span class="spotlight-section-count">${movieList.length}</span>
                </div>
            `;
            movieList.forEach((item) => {
                const element = createMovieItemElement(item);
                section.appendChild(element);
                const itemIndex = visibleItems.length;
                element.addEventListener('mouseenter', () => {
                    selectedIndex = itemIndex;
                    highlightSelectedItem();
                });
                visibleItems.push({ element, data: item });
            });
            resultsContainer.appendChild(section);
        }

        // 3. Quick Actions Section
        if (actionList.length > 0) {
            const section = document.createElement('div');
            section.className = 'spotlight-section';
            section.innerHTML = `
                <div class="spotlight-section-title">
                    <span>Quick Commands</span>
                    <span class="spotlight-section-count">${actionList.length}</span>
                </div>
            `;
            actionList.forEach((item) => {
                const element = createActionItemElement(item);
                section.appendChild(element);
                const itemIndex = visibleItems.length;
                element.addEventListener('mouseenter', () => {
                    selectedIndex = itemIndex;
                    highlightSelectedItem();
                });
                visibleItems.push({ element, data: item });
            });
            resultsContainer.appendChild(section);
        }

        selectedIndex = 0;
        highlightSelectedItem();
    }

    function createAnimeItemElement(item) {
        const div = document.createElement('div');
        div.className = 'spotlight-item';
        div.setAttribute('role', 'option');
        div.setAttribute('aria-selected', 'false');

        const genresText = Array.isArray(item.genres) ? item.genres.join(' · ') : '';
        const statusKind = item.watch_status_kind || 'not_started';
        const statusLabel = item.watch_status_label || '';
        const progressLabel = item.watch_progress_label ? `(${item.watch_progress_label})` : '';

        const STAR_ICON = `<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>`;
        const ENTER_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"></polyline><path d="M20 4v7a4 4 0 0 1-4 4H4"></path></svg>`;

        div.innerHTML = `
            <img class="spotlight-item-poster" src="${escapeHtml(item.poster)}" alt="" loading="lazy">
            <div class="spotlight-item-body">
                <div class="spotlight-item-title-row">
                    <span class="spotlight-item-title">${escapeHtml(item.title)}</span>
                    ${item.score ? `<span class="spotlight-score-badge">${STAR_ICON} ${escapeHtml(item.score)}</span>` : ''}
                </div>
                <div class="spotlight-item-desc">
                    ${statusLabel ? `<span class="spotlight-status-badge ${statusKind}">${escapeHtml(statusLabel)} ${escapeHtml(progressLabel)}</span>` : ''}
                    <span>${item.year ? escapeHtml(item.year) + (genresText ? ' · ' : '') : ''}${escapeHtml(genresText)}</span>
                </div>
            </div>
            <span class="spotlight-item-enter">${ENTER_ICON}</span>
        `;

        div.addEventListener('click', () => executeItem(item));
        return div;
    }

    function createMovieItemElement(item) {
        const div = document.createElement('div');
        div.className = 'spotlight-item';
        div.setAttribute('role', 'option');
        div.setAttribute('aria-selected', 'false');

        const statusKind = item.watch_status_kind || 'not_started';
        const statusLabel = item.watch_status_label || '';
        const STAR_ICON = `<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>`;
        const ENTER_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"></polyline><path d="M20 4v7a4 4 0 0 1-4 4H4"></path></svg>`;

        div.innerHTML = `
            <img class="spotlight-item-poster" src="${escapeHtml(item.poster)}" alt="" loading="lazy">
            <div class="spotlight-item-body">
                <div class="spotlight-item-title-row">
                    <span class="spotlight-item-title">${escapeHtml(item.title)}</span>
                    ${item.score ? `<span class="spotlight-score-badge">${STAR_ICON} ${escapeHtml(item.score)}</span>` : ''}
                </div>
                <div class="spotlight-item-desc">
                    ${statusLabel ? `<span class="spotlight-status-badge ${statusKind}">${escapeHtml(statusLabel)}</span>` : ''}
                    <span>Movie${item.year ? ' · ' + escapeHtml(item.year) : ''}</span>
                </div>
            </div>
            <span class="spotlight-item-enter">${ENTER_ICON}</span>
        `;

        div.addEventListener('click', () => executeItem(item));
        return div;
    }

    function createActionItemElement(item) {
        const div = document.createElement('div');
        div.className = 'spotlight-item';
        div.setAttribute('role', 'option');
        div.setAttribute('aria-selected', 'false');

        const iconSvg = ACTION_ICONS[item.icon] || ACTION_ICONS.home;
        const ENTER_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"></polyline><path d="M20 4v7a4 4 0 0 1-4 4H4"></path></svg>`;

        div.innerHTML = `
            <div class="spotlight-action-icon" aria-hidden="true">${iconSvg}</div>
            <div class="spotlight-item-body">
                <div class="spotlight-item-title-row">
                    <span class="spotlight-item-title">${escapeHtml(item.title)}</span>
                </div>
                <div class="spotlight-item-desc">
                    <span>${escapeHtml(item.subtitle)}</span>
                </div>
            </div>
            <span class="spotlight-item-enter">${ENTER_ICON}</span>
        `;

        div.addEventListener('click', () => executeItem(item));
        return div;
    }

    function handleKeyNavigation(e) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (visibleItems.length === 0) return;
            selectedIndex = (selectedIndex + 1) % visibleItems.length;
            highlightSelectedItem();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (visibleItems.length === 0) return;
            selectedIndex = (selectedIndex - 1 + visibleItems.length) % visibleItems.length;
            highlightSelectedItem();
        } else if (e.key === 'Tab') {
            e.preventDefault();
            if (visibleItems.length === 0) return;
            if (e.shiftKey) {
                selectedIndex = (selectedIndex - 1 + visibleItems.length) % visibleItems.length;
            } else {
                selectedIndex = (selectedIndex + 1) % visibleItems.length;
            }
            highlightSelectedItem();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            executeSelected();
        }
    }

    function highlightSelectedItem() {
        visibleItems.forEach((item, index) => {
            const isSelected = index === selectedIndex;
            item.element.classList.toggle('is-selected', isSelected);
            item.element.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            if (isSelected) {
                item.element.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    function executeSelected() {
        if (visibleItems.length === 0 || selectedIndex < 0 || selectedIndex >= visibleItems.length) return;
        executeItem(visibleItems[selectedIndex].data);
    }

    function executeItem(item) {
        if (!item) return;

        if (item.url === '#action:refresh_library') {
            closeSpotlight();
            triggerLibraryRefresh();
            return;
        }

        if (item.url) {
            closeSpotlight();
            window.location.href = item.url;
        }
    }

    function triggerLibraryRefresh() {
        const token = window.ANIBASE_ACTION_TOKEN || '';
        fetch('/api/library/refresh', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'X-Action-Token': token } : {})
            }
        }).then(r => r.json()).then(data => {
            showToast(data.message || 'Library refresh started in background.', 'info');
        }).catch(() => {
            showToast('Unable to start library refresh.', 'error');
        });
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 100000;
            background: #1e293b;
            color: #f8fafc;
            border: 1px solid rgba(255, 255, 255, 0.15);
            padding: 12px 18px;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            font-size: 0.9rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
            animation: spotlightFadeIn 0.2s ease;
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3200);
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Global Shortcut Listener
    window.addEventListener('keydown', (e) => {
        const isModifier = e.ctrlKey || e.metaKey;
        if (isModifier && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            toggleSpotlight();
        } else if (e.key === 'Escape' && isOpen) {
            e.preventDefault();
            closeSpotlight();
        }
    });

    // Public API
    window.AniBaseSpotlight = {
        open: openSpotlight,
        close: closeSpotlight,
        toggle: toggleSpotlight,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSpotlightDOM);
    } else {
        initSpotlightDOM();
    }
})();

// static/global-mini-player.js
/**
 * Global Persistent Floating Mini Player across Pages for AniBase.
 * Reads playback state from sessionStorage and maintains persistent video playback across page navigation.
 */

(function() {
    'use strict';

    const STORAGE_KEY = 'anibase_persistent_mini_player';

    function getSavedState() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const data = JSON.parse(raw);
            if (data && data.active && data.streamUrl) {
                return data;
            }
        } catch (_) {}
        return null;
    }

    function saveCurrentState(updates = {}) {
        try {
            const current = getSavedState();
            if (!current) return;
            const updated = Object.assign({}, current, updates, { updatedAt: Date.now() });
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
        } catch (_) {}
    }

    function clearSavedState() {
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (_) {}
    }

    function formatTime(seconds) {
        if (!seconds || isNaN(seconds)) return '00:00';
        const sec = Math.floor(seconds);
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        const h = Math.floor(m / 60);
        const remM = m % 60;
        if (h > 0) {
            return `${h}:${String(remM).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function reportWatchProgress(state, video, useKeepalive = false) {
        if (!state || !state.animeName || !state.episodePath || !video || !video.duration) return;
        const duration = video.duration;
        const currentTime = video.currentTime || 0;
        const progress = Math.max(0, Math.min(100, Math.round((currentTime / duration) * 100)));

        const payload = {
            anime_name: state.animeName,
            episode: state.episodePath,
            progress: progress,
            duration: duration,
            current_seconds: currentTime
        };

        const headers = { 'Content-Type': 'application/json' };
        if (window.ANIBASE_ACTION_TOKEN) {
            headers['X-AniBase-Action-Token'] = window.ANIBASE_ACTION_TOKEN;
        }

        try {
            fetch('/api/watch-status/progress', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload),
                keepalive: Boolean(useKeepalive)
            }).catch(() => {});
        } catch (_) {}
    }

    function initGlobalMiniPlayer() {
        // If we are on player page itself, do not mount the global mini player
        if (document.body.classList.contains('player-body') || document.getElementById('videoPlayerContainer')) {
            return;
        }

        const state = getSavedState();
        if (!state) return;

        const container = document.getElementById('globalMiniPlayer');
        const video = document.getElementById('globalMiniVideo');
        if (!container || !video) return;

        const titleEl = document.getElementById('globalMiniPlayerTitle');
        const epEl = document.getElementById('globalMiniPlayerEp');
        const overlay = document.getElementById('globalMiniOverlay');
        const playBtn = document.getElementById('globalMiniPlayBtn');
        const playIcon = document.getElementById('globalMiniPlayIcon');
        const pauseIcon = document.getElementById('globalMiniPauseIcon');
        const muteBtn = document.getElementById('globalMiniMuteBtn');
        const volumeIcon = document.getElementById('globalMiniVolumeIcon');
        const mutedIcon = document.getElementById('globalMiniMutedIcon');
        const seekBackBtn = document.getElementById('globalMiniSeekBackBtn');
        const seekForwardBtn = document.getElementById('globalMiniSeekForwardBtn');
        const expandBtn = document.getElementById('globalMiniPlayerExpandBtn');
        const closeBtn = document.getElementById('globalMiniPlayerCloseBtn');
        const timeDisplay = document.getElementById('globalMiniTimeDisplay');
        const progressTrack = document.getElementById('globalMiniProgressTrack');
        const progressFill = document.getElementById('globalMiniProgressFill');

        // Populate metadata
        if (titleEl) titleEl.textContent = state.displayTitle || state.animeName;
        if (epEl) {
            epEl.textContent = state.episodeDisplayName || (state.isMovie ? 'Movie' : (state.episodeNum ? `Episode ${state.episodeNum}` : ''));
        }

        // Setup stream source
        video.crossOrigin = 'anonymous';
        video.src = state.streamUrl;
        if (state.subtitleUrl) {
            const track = document.createElement('track');
            track.kind = 'subtitles';
            track.src = state.subtitleUrl;
            track.srclang = 'und';
            track.default = true;
            video.appendChild(track);
            track.addEventListener('load', () => {
                if (video.textTracks && video.textTracks[0]) {
                    video.textTracks[0].mode = 'showing';
                }
            });
        }

        // Volume & Mute
        video.volume = (typeof state.volume === 'number' && !isNaN(state.volume)) ? state.volume : 1;
        video.muted = Boolean(state.muted);
        updateMuteUI();

        // Reveal container
        container.hidden = false;

        // Restore position and play
        const targetTime = parseFloat(state.currentTime) || 0;
        let hasRestoredTime = false;

        function restorePlayback() {
            if (!hasRestoredTime) {
                if (targetTime > 0) {
                    try {
                        video.currentTime = targetTime;
                        hasRestoredTime = true;
                    } catch (_) {}
                } else {
                    hasRestoredTime = true;
                }
            }

            if (!state.paused) {
                const playPromise = video.play();
                if (playPromise !== undefined) {
                    playPromise.catch((err) => {
                        // If autoplay blocked by browser due to audio, fallback to muted autoplay
                        if (err && (err.name === 'NotAllowedError' || err.name === 'AutoplayBlocked')) {
                            video.muted = true;
                            updateMuteUI();
                            video.play().catch(() => {});
                        }
                    });
                }
            }
        }

        if (video.readyState >= 1) {
            restorePlayback();
        } else {
            video.addEventListener('loadedmetadata', restorePlayback, { once: true });
        }
        video.addEventListener('canplay', () => {
            if (targetTime > 0 && Math.abs((video.currentTime || 0) - targetTime) > 1.5) {
                try {
                    video.currentTime = targetTime;
                } catch (_) {}
            }
        }, { once: true });

        // Playback state updates
        function updatePlayPauseUI() {
            const isPlaying = !video.paused && !video.ended;
            if (playIcon) playIcon.hidden = isPlaying;
            if (pauseIcon) pauseIcon.hidden = !isPlaying;
            if (overlay) overlay.classList.toggle('is-paused', !isPlaying);
            saveCurrentState({ paused: video.paused, currentTime: video.currentTime });
        }

        function updateMuteUI() {
            if (volumeIcon) volumeIcon.hidden = video.muted;
            if (mutedIcon) mutedIcon.hidden = !video.muted;
            saveCurrentState({ muted: video.muted, volume: video.volume });
        }

        function updateProgressUI() {
            if (!video.duration || isNaN(video.duration)) return;
            const cur = video.currentTime || 0;
            const dur = video.duration || 1;
            const pct = Math.max(0, Math.min(100, (cur / dur) * 100));

            if (progressFill) progressFill.style.width = `${pct}%`;
            if (progressTrack) progressTrack.setAttribute('aria-valuenow', Math.round(pct));
            if (timeDisplay) timeDisplay.textContent = `${formatTime(cur)} / ${formatTime(dur)}`;
        }

        video.addEventListener('play', updatePlayPauseUI);
        video.addEventListener('pause', updatePlayPauseUI);
        video.addEventListener('volumechange', updateMuteUI);
        video.addEventListener('timeupdate', updateProgressUI);

        // Periodic scrobble/progress reporting every 20 seconds
        let lastReportTime = 0;
        video.addEventListener('timeupdate', () => {
            const now = Date.now();
            if (now - lastReportTime > 20000) {
                lastReportTime = now;
                saveCurrentState({ currentTime: video.currentTime });
                reportWatchProgress(state, video, false);
            }
        });

        // Toggle Play/Pause
        if (playBtn) {
            playBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (video.paused) {
                    video.play().catch(() => {});
                } else {
                    video.pause();
                }
            });
        }

        // Seek buttons
        if (seekBackBtn) {
            seekBackBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                video.currentTime = Math.max(0, (video.currentTime || 0) - 10);
            });
        }
        if (seekForwardBtn) {
            seekForwardBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                video.currentTime = Math.min((video.duration || 999999), (video.currentTime || 0) + 10);
            });
        }

        // Mute button
        if (muteBtn) {
            muteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                video.muted = !video.muted;
            });
        }

        // Progress bar click to seek
        if (progressTrack) {
            progressTrack.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!video.duration) return;
                const rect = progressTrack.getBoundingClientRect();
                const clickX = e.clientX - rect.left;
                const ratio = Math.max(0, Math.min(1, clickX / rect.width));
                video.currentTime = ratio * video.duration;
            });
        }

        // Expand to Full Player
        function expandToFullPlayer() {
            const currentPosition = Math.max(0, Math.floor(video.currentTime || 0));
            reportWatchProgress(state, video, true);

            // Clean state so full player doesn't conflict
            clearSavedState();

            let targetUrl = state.playerUrl || `/player?anime_name=${encodeURIComponent(state.animeName)}&episode=${encodeURIComponent(state.episodePath)}`;
            const urlObj = new URL(targetUrl, window.location.origin);
            urlObj.searchParams.set('t', String(currentPosition));

            window.location.href = urlObj.pathname + urlObj.search;
        }

        if (expandBtn) {
            expandBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                expandToFullPlayer();
            });
        }

        // Close Mini Player
        function closeMiniPlayer() {
            video.pause();
            reportWatchProgress(state, video, true);
            clearSavedState();

            container.classList.add('is-hiding');
            setTimeout(() => {
                container.hidden = true;
                container.classList.remove('is-hiding');
                video.src = '';
            }, 200);
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeMiniPlayer();
            });
        }

        // Draggable floating mini player & Cinema size toggle
        const dragHandle = document.getElementById('globalMiniDragHandle') || container;
        let isDragging = false;
        let startX = 0;
        let startY = 0;
        let initialLeft = 0;
        let initialTop = 0;

        if (state.customPos && typeof state.customPos.left === 'number' && typeof state.customPos.top === 'number') {
            container.style.left = `${Math.max(12, Math.min(window.innerWidth - 380 - 12, state.customPos.left))}px`;
            container.style.top = `${Math.max(12, Math.min(window.innerHeight - 214 - 12, state.customPos.top))}px`;
            container.style.right = 'auto';
            container.style.bottom = 'auto';
        }

        if (state.isLarge) {
            container.classList.add('is-large');
        }

        if (dragHandle) {
            dragHandle.addEventListener('dblclick', (e) => {
                if (e.target.closest('button, a')) return;
                container.classList.toggle('is-large');
                saveCurrentState({ isLarge: container.classList.contains('is-large') });
            });

            dragHandle.addEventListener('pointerdown', (e) => {
                if (e.target.closest('button, a')) return;
                isDragging = true;
                container.classList.add('is-dragging');
                try {
                    dragHandle.setPointerCapture(e.pointerId);
                } catch (_) {}

                const rect = container.getBoundingClientRect();
                startX = e.clientX;
                startY = e.clientY;
                initialLeft = rect.left;
                initialTop = rect.top;

                container.style.left = `${rect.left}px`;
                container.style.top = `${rect.top}px`;
                container.style.right = 'auto';
                container.style.bottom = 'auto';
            });

            dragHandle.addEventListener('pointermove', (e) => {
                if (!isDragging) return;
                const dx = e.clientX - startX;
                const dy = e.clientY - startY;

                let nextLeft = initialLeft + dx;
                let nextTop = initialTop + dy;

                const maxLeft = Math.max(12, window.innerWidth - container.offsetWidth - 12);
                const maxTop = Math.max(12, window.innerHeight - container.offsetHeight - 12);

                nextLeft = Math.max(12, Math.min(maxLeft, nextLeft));
                nextTop = Math.max(12, Math.min(maxTop, nextTop));

                container.style.left = `${nextLeft}px`;
                container.style.top = `${nextTop}px`;
            });

            const stopDrag = (e) => {
                if (!isDragging) return;
                isDragging = false;
                container.classList.remove('is-dragging');
                try {
                    dragHandle.releasePointerCapture(e.pointerId);
                } catch (_) {}

                const rect = container.getBoundingClientRect();
                saveCurrentState({
                    customPos: { left: rect.left, top: rect.top },
                    isLarge: container.classList.contains('is-large')
                });
            };

            dragHandle.addEventListener('pointerup', stopDrag);
            dragHandle.addEventListener('pointercancel', stopDrag);
        }

        // Save position on page navigation across site
        function handleUnload() {
            if (!container.hidden && video) {
                const rect = container.getBoundingClientRect();
                const hasCustomPos = container.style.left !== '' && container.style.left !== 'auto';
                saveCurrentState({
                    currentTime: video.currentTime || 0,
                    paused: video.paused,
                    volume: video.volume,
                    muted: video.muted,
                    customPos: hasCustomPos ? { left: rect.left, top: rect.top } : null,
                    isLarge: container.classList.contains('is-large')
                });
                reportWatchProgress(state, video, true);
            }
        }

        window.addEventListener('beforeunload', handleUnload);
        window.addEventListener('pagehide', handleUnload);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initGlobalMiniPlayer);
    } else {
        initGlobalMiniPlayer();
    }
})();


const videoElement = document.getElementById('player');
const sourceElement = videoElement.querySelector('source');
let subtitleTrack = videoElement.querySelector('track');
const episodeLine = document.getElementById('playerEpisodeLine');
const prevEpisodeLink = document.getElementById('prevEpisodeLink');
const nextEpisodeLink = document.getElementById('nextEpisodeLink');
const openMxPlayerBtn = document.getElementById('openMxPlayerBtn');
const episodeSwitchNotice = document.getElementById('episodeSwitchNotice');
const vlcToastActionBtn = document.getElementById('vlcToastActionBtn');
const MX_PLAYER_PACKAGE = 'com.mxtech.videoplayer.pro';
let isSoftSwitching = false;
const RPC_SESSION_ID = (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `player-${Date.now()}-${Math.random().toString(16).slice(2)}`;

function actionHeaders(extra = {}) {
    const headers = { ...extra };
    if (window.ANIBASE_ACTION_TOKEN) {
        headers['X-AniBase-Action-Token'] = window.ANIBASE_ACTION_TOKEN;
    }
    return headers;
}

// 1. Inisialisasi Plyr
const player = new Plyr(videoElement, {
    title: window.ANIME_NAME,
    autoplay: true,
    iconUrl: window.PLYR_ICON_URL || '/static/plyr.svg',
    keyboard: { focused: true, global: true },
    captions: { active: true, update: true, language: 'id' }
});

// Pindahkan toast ke dalam kontainer Plyr agar terlihat saat fullscreen
player.on('ready', () => {
    const plyrContainer = document.querySelector('.plyr');
    const toast = document.getElementById('screenshotToast');
    if (plyrContainer && toast) {
        plyrContainer.appendChild(toast);
    }

    // Fitur Resume: Lanjutkan dari detik terakhir jika ada
    if (window.RESUME_TIME > 0) {
        // Gunakan event 'canplay' agar seeking dilakukan saat video sudah siap
        player.once('canplay', () => {
            player.currentTime = window.RESUME_TIME;
        });
    }
});

videoElement.addEventListener('error', () => {
    const error = videoElement.error;
    console.error('Video Error Code:', error ? error.code : 'unknown', 'Message:', error ? error.message : '');
    hideEpisodeSwitchNotice();

    const toastOptions = window.VLC_AVAILABLE
        ? { actionLabel: 'Open in Media Player', onAction: playInVLC, duration: 6000 }
        : { actionLabel: 'Settings', onAction: openSettingsPage, duration: 6000 };
    const message = window.VLC_AVAILABLE
        ? 'Browser cannot play this codec. Try Media Player.'
        : 'Browser cannot play this codec. Configure Media Player in Settings.';

    showVlcToast(message, toastOptions);

    const playerContainer = document.querySelector('.video-player-container');
    if (playerContainer) {
        playerContainer.style.borderColor = '#ef4444';
    }
});

// Auto Next
player.on('ended', () => {
    if (window.NEXT_EP_PATH && window.NEXT_EP_PATH !== 'None') {
        switchEpisode(window.NEXT_EP_PATH, { autoplay: true });
    }
});

let rpcInterval;
let watchProgressInterval;

function formatTime(seconds) {
    if (isNaN(seconds)) return "00:00";
    if (seconds < 0) return "00:00"; // Tangani nilai negatif atau NaN
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function updateStatus() {
    const currentTime = player.currentTime;
    const duration = player.duration;
    const timeStr = `${formatTime(currentTime)} / ${formatTime(duration)}`;

    fetch('/update_progress', {
        method: 'POST',
        headers: actionHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            anime_name: window.ANIME_NAME,
            episode: window.EPISODE_PATH,
            episode_num: window.CURRENT_EP_NUM,
            time_str: timeStr,
            last_seconds: currentTime,
            duration: duration,
            rpc_session_id: RPC_SESSION_ID
        })
    }).then((response) => {
        if (!response.ok) {
            throw new Error('Progress update failed');
        }
    }).catch(() => {});
}

function sendWatchProgress(useKeepalive = false) {
    const currentTime = player.currentTime || 0;
    const duration = player.duration || 0;

    if (!window.ANIME_NAME || !window.EPISODE_PATH || !duration || isNaN(duration)) {
        return;
    }

    const progress = Math.round((currentTime / duration) * 100);
    const payload = {
        anime_name: window.ANIME_NAME,
        episode: window.EPISODE_PATH,
        progress: Math.max(0, Math.min(100, progress)),
        duration: duration,
        current_seconds: currentTime
    };

    fetch('/api/watch-status/progress', {
        method: 'POST',
        headers: actionHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
        keepalive: useKeepalive
    }).then((response) => {
        if (!response.ok) {
            throw new Error('Watch progress update failed');
        }
    }).catch(() => {});
}

function encodeEpisodePath(path) {
    return path.split('/').map(encodeURIComponent).join('/');
}

function buildEpisodeRoute(template, fallbackTemplate, animeName, episodePath) {
    const routeTemplate = template || fallbackTemplate;
    return routeTemplate
        .replace('__ANIME__', encodeURIComponent(animeName))
        .replace('__EPISODE__', encodeEpisodePath(episodePath));
}

function getPlayerUrl(episodePath) {
    return buildEpisodeRoute(
        window.PLAYER_URL_TEMPLATE,
        '/player/__ANIME__/__EPISODE__',
        window.ANIME_NAME,
        episodePath
    );
}

function getStreamUrl(episodePath) {
    return buildEpisodeRoute(
        window.STREAM_URL_TEMPLATE,
        '/stream/__ANIME__/__EPISODE__',
        window.ANIME_NAME,
        episodePath
    );
}

function getSubtitleUrl(episodePath) {
    return buildEpisodeRoute(
        window.SUBTITLE_URL_TEMPLATE,
        '/subtitle/__ANIME__/__EPISODE__',
        window.ANIME_NAME,
        episodePath
    );
}

function getPlayUrl(episodePath) {
    return buildEpisodeRoute(
        window.PLAY_URL_TEMPLATE,
        '/play/__ANIME__/__EPISODE__',
        window.ANIME_NAME,
        episodePath
    );
}

function isAndroidDevice() {
    return /Android/i.test(navigator.userAgent || '');
}

function getActiveStreamUrl() {
    const sourceUrl = sourceElement && sourceElement.getAttribute('src');
    const streamUrl = sourceUrl || getStreamUrl(window.EPISODE_PATH);
    return new URL(streamUrl, window.location.href).href;
}

function buildMxPlayerIntentUrl(streamUrl) {
    const parsedUrl = new URL(streamUrl);
    const scheme = parsedUrl.protocol.replace(':', '');
    const intentPath = parsedUrl.href.replace(`${parsedUrl.protocol}//`, '');

    return `intent://${intentPath}#Intent;scheme=${scheme};package=${MX_PLAYER_PACKAGE};action=android.intent.action.VIEW;type=video/*;S.browser_fallback_url=${encodeURIComponent(streamUrl)};end`;
}

function openInMxPlayer() {
    const streamUrl = getActiveStreamUrl();
    const intentUrl = buildMxPlayerIntentUrl(streamUrl);

    window.location.href = intentUrl;
}

function getEpisodeCards() {
    return Array.from(document.querySelectorAll('.player-episode-card[data-episode-path]'));
}

function getEpisodeState(episodePath) {
    const cards = getEpisodeCards();
    const index = cards.findIndex(card => card.dataset.episodePath === episodePath);
    const card = index >= 0 ? cards[index] : null;

    return {
        cards,
        index,
        card,
        previous: index > 0 ? cards[index - 1] : null,
        next: index >= 0 && index < cards.length - 1 ? cards[index + 1] : null
    };
}

function updateEpisodeLink(link, card) {
    if (!link) return;

    if (!card) {
        link.hidden = true;
        link.setAttribute('aria-disabled', 'true');
        link.href = '#';
        link.dataset.episodePath = '';
        return;
    }

    link.hidden = false;
    link.removeAttribute('aria-disabled');
    link.href = card.href;
    link.dataset.episodePath = card.dataset.episodePath;
}

function updatePlayerEpisodeState(episodePath) {
    const state = getEpisodeState(episodePath);

    if (!state.card) {
        return null;
    }

    state.cards.forEach(card => card.classList.remove('active'));
    state.card.classList.add('active');

    const episodeGrid = state.card.closest('.player-episode-grid');
    if (episodeGrid) {
        const centeredLeft = state.card.offsetLeft
            - ((episodeGrid.clientWidth - state.card.offsetWidth) / 2);
        episodeGrid.scrollTo({
            left: Math.max(0, centeredLeft),
            behavior: 'smooth'
        });
    }

    window.EPISODE_PATH = episodePath;
    window.CURRENT_EP_NUM = state.card.dataset.episodeNum || '';
    window.CURRENT_EP_LABEL = state.card.dataset.episodeLabel || window.CURRENT_EP_NUM || 'Unknown';
    window.CURRENT_POSITION = state.card.dataset.listPosition || String(state.index + 1);
    window.NEXT_EP_PATH = state.next ? state.next.dataset.episodePath : null;
    window.RESUME_TIME = 0;

    if (episodeLine) {
        episodeLine.textContent = `Episode ${window.CURRENT_EP_LABEL} (${window.CURRENT_POSITION} of ${window.TOTAL_EPISODES || state.cards.length} available)`;
    }

    updateEpisodeLink(prevEpisodeLink, state.previous);
    updateEpisodeLink(nextEpisodeLink, state.next);

    return state;
}

function replaceSubtitleTrack(src) {
    if (!subtitleTrack) return;

    const newTrack = subtitleTrack.cloneNode(false);
    newTrack.src = src;
    subtitleTrack.remove();
    videoElement.appendChild(newTrack);
    subtitleTrack = newTrack;
}

function showEpisodeSwitchNotice(message) {
    if (!episodeSwitchNotice) return;

    episodeSwitchNotice.textContent = message;
    episodeSwitchNotice.hidden = false;
}

function hideEpisodeSwitchNotice() {
    if (!episodeSwitchNotice) return;

    episodeSwitchNotice.hidden = true;
}

function switchEpisode(episodePath, options = {}) {
    if (!episodePath || episodePath === window.EPISODE_PATH || !sourceElement) {
        return false;
    }

    const state = getEpisodeState(episodePath);
    if (!state.card) {
        return false;
    }

    const shouldPushState = options.pushState !== false;
    const shouldAutoplay = options.autoplay !== undefined ? options.autoplay : !player.paused;
    const playerUrl = getPlayerUrl(episodePath);

    sendWatchProgress();
    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);

    isSoftSwitching = true;
    updatePlayerEpisodeState(episodePath);
    showEpisodeSwitchNotice(`Loading Episode ${window.CURRENT_EP_LABEL || window.CURRENT_EP_NUM}...`);

    player.once('canplay', () => {
        isSoftSwitching = false;
        hideEpisodeSwitchNotice();
        const playerContainer = document.querySelector('.video-player-container');
        if (playerContainer) {
            playerContainer.style.borderColor = '';
        }
        updateStatus();
        sendWatchProgress();

        if (shouldAutoplay) {
            player.play().catch(() => {});
        }
    });

    sourceElement.src = getStreamUrl(episodePath);
    replaceSubtitleTrack(getSubtitleUrl(episodePath));
    videoElement.load();

    window.setTimeout(() => {
        isSoftSwitching = false;
        hideEpisodeSwitchNotice();
    }, 15000);

    if (shouldPushState) {
        history.pushState({ episodePath }, '', playerUrl);
    }

    return true;
}

// Fungsi untuk menghapus status Discord
function clearStatus() {
    fetch('/clear_rpc', {
        method: 'POST',
        headers: actionHeaders({ 'X-AniBase-RPC-Session': RPC_SESSION_ID })
    }).then((response) => {
        if (!response.ok) {
            throw new Error('Clear RPC failed');
        }
    }).catch(() => {});
}

player.on('play', () => {
    updateStatus();
    sendWatchProgress();
    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);
    // Update status ke Discord setiap 15 detik agar sinkron (Limit Discord RPC)
    rpcInterval = setInterval(updateStatus, 15000);
    watchProgressInterval = setInterval(sendWatchProgress, 30000);
});

player.on('pause', () => {
    if (isSoftSwitching) {
        return;
    }

    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);
    sendWatchProgress();
    clearStatus();
});

// Hapus status Discord saat pengguna menutup tab atau pindah halaman
window.addEventListener('beforeunload', () => {
    sendWatchProgress(true);
    fetch('/clear_rpc', {
        method: 'POST',
        headers: actionHeaders({ 'X-AniBase-RPC-Session': RPC_SESSION_ID }),
        keepalive: true
    }).then((response) => {
        if (!response.ok) {
            throw new Error('Clear RPC failed');
        }
    }).catch(() => {});
});

history.replaceState({ episodePath: window.EPISODE_PATH }, '', window.location.href);

if (openMxPlayerBtn && isAndroidDevice()) {
    openMxPlayerBtn.hidden = false;
    openMxPlayerBtn.addEventListener('click', openInMxPlayer);
}

document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-player-nav]');
    if (!link || link.getAttribute('aria-disabled') === 'true') {
        return;
    }

    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
        return;
    }

    const episodePath = link.dataset.episodePath;
    if (!episodePath) {
        return;
    }

    event.preventDefault();

    if (!switchEpisode(episodePath)) {
        window.location.href = link.href;
    }
});

window.addEventListener('popstate', (event) => {
    const episodePath = event.state && event.state.episodePath;
    if (episodePath) {
        switchEpisode(episodePath, {
            pushState: false,
            autoplay: false
        });
    }
});

function playInVLC() {
    const url = getPlayUrl(window.EPISODE_PATH);

    fetch(url, {
        method: 'POST',
        headers: actionHeaders()
    })
        .then(async res => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw data;
            }
            return data;
        })
        .then(data => {
            if(data.status === 'playing') {
                player.pause();
                showVlcToast('Opening video in Media Player...');
            } else if (data.message) {
                const needsSettings = data.status === 'vlc_not_configured' || data.status === 'vlc_not_found';
                showVlcToast(data.message, needsSettings ? {
                    actionLabel: 'Settings',
                    onAction: openSettingsPage,
                    duration: 6000
                } : {});
            } else {
                showVlcToast('Unable to open Media Player');
            }
        })
        .catch((error) => {
            showVlcToast(error && error.message ? error.message : 'Unable to contact server');
        });
}

function openSettingsPage() {
    window.location.href = '/settings';
}

function showToast(toastId, messageId, actionButton, message, options = {}) {
    const toast = document.getElementById(toastId);
    const msg = document.getElementById(messageId);
    if (!toast || !msg) return;

    msg.textContent = message;

    if (actionButton) {
        actionButton.hidden = true;
        actionButton.onclick = null;

        if (options.actionLabel && typeof options.onAction === 'function') {
            actionButton.textContent = options.actionLabel;
            actionButton.onclick = options.onAction;
            actionButton.hidden = false;
        }
    }

    toast.classList.add('show');

    if (toast.hideTimer) {
        clearTimeout(toast.hideTimer);
    }
    
    // Sembunyikan notifikasi setelah 3 detik
    toast.hideTimer = setTimeout(() => {
        toast.classList.remove('show');
    }, options.duration || 3000);
}

function showScreenshotToast(message, options = {}) {
    showToast('screenshotToast', 'screenshotToastMessage', null, message, options);
}

function showVlcToast(message, options = {}) {
    showToast('vlcToast', 'vlcToastMessage', vlcToastActionBtn, message, options);
}

const PLAYBACK_SPEED_STEPS = [1, 1.25, 1.5, 1.75, 2];

function changePlaybackSpeed(direction) {
    const currentSpeed = Number(player.speed || videoElement.playbackRate || 1);
    const epsilon = 0.001;
    let nextSpeed;

    if (direction > 0) {
        nextSpeed = PLAYBACK_SPEED_STEPS.find(speed => speed > currentSpeed + epsilon);
        nextSpeed = nextSpeed === undefined ? PLAYBACK_SPEED_STEPS.at(-1) : nextSpeed;
    } else {
        nextSpeed = [...PLAYBACK_SPEED_STEPS].reverse().find(speed => speed < currentSpeed - epsilon);
        nextSpeed = nextSpeed === undefined ? PLAYBACK_SPEED_STEPS[0] : nextSpeed;
    }

    player.speed = nextSpeed;
    showScreenshotToast(`Playback speed: ${nextSpeed}x`);
}

window.addEventListener('keydown', (event) => {
    const target = event.target;
    const isTyping = target && (
        target.isContentEditable ||
        (target.matches && target.matches('input, textarea, select'))
    );
    const isPlus = event.key === '+' || event.code === 'NumpadAdd';
    const isMinus = event.key === '-' || event.code === 'NumpadSubtract';

    if ((!isPlus && !isMinus) || event.ctrlKey || event.metaKey || event.altKey || event.repeat || isTyping) {
        return;
    }

    event.preventDefault();
    changePlaybackSpeed(isPlus ? 1 : -1);
});

// Fitur Screenshot dengan tombol 's'
window.addEventListener('keydown', (e) => {
    // Pastikan tidak sedang mengetik di input search
    if (e.key.toLowerCase() === 's' && e.target.tagName !== 'INPUT') {
        e.preventDefault(); // Mencegah perilaku default browser (misal: search)
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        
        const imageData = canvas.toDataURL('image/png');
        
        fetch('/screenshot', {
            method: 'POST',
            headers: actionHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ image: imageData })
        })
        .then(async res => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw data;
            }
            return data;
        })
        .then(data => {
            if(data.status === 'success') {
                showScreenshotToast('Screenshot saved');
                console.log('Screenshot saved to:', data.path);
            } else {
                showScreenshotToast(data.message || 'Unable to save screenshot');
            }
        })
        .catch(err => {
            showScreenshotToast(err && err.message ? err.message : 'A system error occurred while saving');
            console.error('Unable to save screenshot:', err);
        });
    }
});

const grid = document.getElementById("episodeGrid");

document.getElementById("scrollLeft").onclick = function() {
    const scrollAmount = grid.clientWidth * 0.8;
    grid.scrollBy({
        left: -scrollAmount,
        behavior: "smooth"
    });
};

document.getElementById("scrollRight").onclick = function() {
    const scrollAmount = grid.clientWidth * 0.8;
    grid.scrollBy({
        left: scrollAmount,
        behavior: "smooth"
    });
};

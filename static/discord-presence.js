// Shared lifecycle for the episode player and the movie player.
window.AniBaseDiscordPresence = class {
    constructor(video, describe) {
        const session = window.crypto?.randomUUID?.() || `presence-${Date.now()}-${Math.random()}`;
        let sequence = 0;
        let started = false;
        let timer;
        let syncTimer;
        const send = (event, keepalive = false) => {
            fetch('/api/discord/presence', {
                method: 'POST', keepalive,
                headers: { 'Content-Type': 'application/json',
                    'X-AniBase-Action-Token': window.ANIBASE_ACTION_TOKEN || '' },
                body: JSON.stringify({ ...describe(), session, sequence: ++sequence, event,
                    position: Number.isFinite(video.currentTime) ? video.currentTime : 0,
                    duration: Number.isFinite(video.duration) ? video.duration : 0,
                    speed: video.playbackRate || 1 })
            }).catch(() => {});
        };
        const stop = (event, keepalive = false) => {
            clearInterval(timer);
            clearTimeout(syncTimer);
            if (started) send(event, keepalive);
            started = false;
        };
        video.addEventListener('playing', () => {
            started = true;
            send('playing');
            clearInterval(timer);
            timer = setInterval(() => {
                if (!video.paused && !video.ended) send('heartbeat');
            }, 15000);
        });
        ['pause', 'ended', 'waiting', 'emptied', 'error'].forEach(event => {
            video.addEventListener(event, () => stop(event === 'emptied' || event === 'error' ? 'stop' : event));
        });
        ['seeked', 'ratechange'].forEach(event => video.addEventListener(event, () => {
            clearTimeout(syncTimer);
            syncTimer = setTimeout(() => {
                if (started && !video.paused && !video.ended) send('sync');
            }, 300);
        }));
        window.addEventListener('pagehide', () => stop('stop', true));
        window.addEventListener('pageshow', event => {
            if (event.persisted && !video.paused && !video.ended) {
                video.dispatchEvent(new Event('playing'));
            }
        });
        if (!video.paused && video.readyState >= 3) video.dispatchEvent(new Event('playing'));
    }
};

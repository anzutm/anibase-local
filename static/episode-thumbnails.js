(() => {
    const thumbnails = Array.from(document.querySelectorAll('img[data-thumbnail-src]'));
    if (!thumbnails.length) return;

    const MAX_CONCURRENT = 2;
    const MAX_ATTEMPTS = 7;
    const RETRY_DELAYS = [1500, 3000, 6000, 12000, 24000, 30000];
    const PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
    const queue = [];
    let activeLoads = 0;
    let stopped = false;

    function retryUrl(url, attempt) {
        if (!attempt) return url;
        const separator = url.includes('?') ? '&' : '?';
        return `${url}${separator}thumbnail_retry=${attempt}`;
    }

    function pumpQueue() {
        if (stopped) return;
        while (activeLoads < MAX_CONCURRENT && queue.length) {
            const image = queue.shift();
            if (!image?.isConnected || image.dataset.thumbnailState !== 'queued') continue;
            loadThumbnail(image);
        }
    }

    function enqueue(image) {
        if (stopped || !image?.isConnected) return;
        const state = image.dataset.thumbnailState;
        if (state === 'queued' || state === 'loading' || state === 'ready') return;
        image.dataset.thumbnailState = 'queued';
        queue.push(image);
        pumpQueue();
    }

    function scheduleRetry(image) {
        const attempt = Number(image.dataset.thumbnailAttempt || 0);
        if (attempt >= MAX_ATTEMPTS || stopped) {
            image.dataset.thumbnailState = 'unavailable';
            return;
        }
        const delay = RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)];
        image.dataset.thumbnailState = 'waiting';
        window.setTimeout(() => enqueue(image), delay);
    }

    function loadThumbnail(image) {
        const source = image.dataset.thumbnailSrc;
        if (!source) {
            image.dataset.thumbnailState = 'unavailable';
            return;
        }

        const attempt = Number(image.dataset.thumbnailAttempt || 0);
        activeLoads += 1;
        image.dataset.thumbnailState = 'loading';
        image.loading = 'eager';

        let settled = false;
        const finish = (succeeded) => {
            if (settled) return;
            settled = true;
            window.clearTimeout(timeout);
            image.onload = null;
            image.onerror = null;
            activeLoads = Math.max(0, activeLoads - 1);

            if (succeeded && image.naturalWidth > 1) {
                image.dataset.thumbnailState = 'ready';
                image.removeAttribute('data-thumbnail-attempt');
            } else {
                image.src = PLACEHOLDER;
                image.dataset.thumbnailAttempt = String(attempt + 1);
                scheduleRetry(image);
            }
            pumpQueue();
        };
        const timeout = window.setTimeout(() => finish(false), 45000);

        image.onload = () => finish(true);
        image.onerror = () => finish(false);
        image.src = retryUrl(source, attempt);
    }

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                enqueue(entry.target);
            });
        }, { rootMargin: '320px' });
        thumbnails.forEach(image => observer.observe(image));
    } else {
        thumbnails.forEach(enqueue);
    }

    window.addEventListener('pagehide', (event) => {
        if (event.persisted) return;
        stopped = true;
        queue.length = 0;
    });
})();

(() => {
    const MAX_CONCURRENT = 2;
    const MAX_ATTEMPTS = 7;
    const RETRY_DELAYS = [1500, 3000, 6000, 12000, 24000, 30000];
    const PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
    const queue = [];
    const activeLoads = new Set();
    let stopped = false;

    function retryUrl(url, attempt) {
        if (!attempt) return url;
        const separator = url.includes('?') ? '&' : '?';
        return `${url}${separator}thumbnail_retry=${attempt}`;
    }

    function pumpQueue() {
        if (stopped) return;

        // Clean up any active loads whose elements are no longer connected to DOM
        for (const activeImg of activeLoads) {
            if (!activeImg.isConnected) {
                activeImg.onload = null;
                activeImg.onerror = null;
                try {
                    activeImg.src = PLACEHOLDER;
                } catch (_) {}
                activeLoads.delete(activeImg);
            }
        }

        while (activeLoads.size < MAX_CONCURRENT && queue.length) {
            const image = queue.shift();
            if (!image?.isConnected || image.dataset.thumbnailState !== 'queued') continue;
            loadThumbnail(image);
        }
    }

    function enqueue(image) {
        if (stopped || !image?.isConnected) return;
        const source = image.dataset.thumbnailSrc;
        if (!source) {
            image.dataset.thumbnailState = 'unavailable';
            return;
        }

        // If already genuinely ready, no need to re-enqueue
        if (
            image.dataset.thumbnailState === 'ready' &&
            image.src &&
            !image.src.startsWith('data:image/gif') &&
            (image.complete ? image.naturalWidth > 1 : true)
        ) {
            return;
        }

        // Avoid adding the same image to queue multiple times
        if (queue.includes(image) || activeLoads.has(image)) {
            return;
        }

        image.dataset.thumbnailState = 'queued';
        queue.push(image);
        pumpQueue();
    }

    function scheduleRetry(image) {
        if (!image?.isConnected || stopped) return;
        const attempt = Number(image.dataset.thumbnailAttempt || 0);
        if (attempt >= MAX_ATTEMPTS) {
            image.dataset.thumbnailState = 'unavailable';
            return;
        }
        const delay = RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)];
        image.dataset.thumbnailState = 'waiting';
        window.setTimeout(() => {
            if (image.isConnected && !stopped) {
                enqueue(image);
            }
        }, delay);
    }

    function loadThumbnail(image) {
        const source = image.dataset.thumbnailSrc;
        if (!source || !image.isConnected) {
            image.dataset.thumbnailState = 'unavailable';
            return;
        }

        const attempt = Number(image.dataset.thumbnailAttempt || 0);
        activeLoads.add(image);
        image.dataset.thumbnailState = 'loading';
        image.loading = 'eager';

        let settled = false;
        const finish = (succeeded) => {
            if (settled) return;
            settled = true;
            window.clearTimeout(timeout);
            image.onload = null;
            image.onerror = null;
            activeLoads.delete(image);

            if (!image.isConnected) {
                pumpQueue();
                return;
            }

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

    let observer = null;
    if ('IntersectionObserver' in window) {
        observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                enqueue(entry.target);
            });
        }, { rootMargin: '320px' });
    }

    function observeThumbnail(image) {
        if (!image || !image.isConnected || stopped) return;
        const source = image.dataset.thumbnailSrc;
        if (!source) return;

        // If the element already has a successfully loaded thumbnail, skip
        if (
            image.dataset.thumbnailState === 'ready' &&
            image.src &&
            !image.src.startsWith('data:image/gif')
        ) {
            if (!image.complete) {
                image.addEventListener('error', () => {
                    image.src = PLACEHOLDER;
                    delete image.dataset.thumbnailState;
                    observeThumbnail(image);
                }, { once: true });
                return;
            }
            if (image.naturalWidth > 1) {
                return;
            }
        }

        // If restored from serialized HTML cache with stale non-ready state, reset it
        if (!queue.includes(image) && !activeLoads.has(image)) {
            if (image.dataset.thumbnailState !== 'ready') {
                delete image.dataset.thumbnailState;
            } else if (!image.src || image.src.startsWith('data:image/gif') || (image.complete && image.naturalWidth <= 1)) {
                delete image.dataset.thumbnailState;
            }
        }

        if (observer) {
            observer.observe(image);
        } else {
            enqueue(image);
        }
    }

    function initEpisodeThumbnails(root = document) {
        if (stopped) return;
        const scope = (root && root.nodeType === Node.ELEMENT_NODE) ? root : document;
        const list = [];
        if (scope.matches && scope.matches('img[data-thumbnail-src]')) {
            list.push(scope);
        }
        if (scope.querySelectorAll) {
            list.push(...Array.from(scope.querySelectorAll('img[data-thumbnail-src]')));
        }
        list.forEach(observeThumbnail);
    }

    if ('MutationObserver' in window) {
        const mutationObserver = new MutationObserver((mutations) => {
            if (stopped) return;
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== Node.ELEMENT_NODE) continue;
                    if (node.matches && node.matches('img[data-thumbnail-src]')) {
                        observeThumbnail(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('img[data-thumbnail-src]').forEach(observeThumbnail);
                    }
                }
            }
        });

        const startObserving = () => {
            const target = document.body || document.documentElement;
            if (target) {
                mutationObserver.observe(target, { childList: true, subtree: true });
            }
        };

        if (document.body) {
            startObserving();
        } else {
            document.addEventListener('DOMContentLoaded', startObserving, { once: true });
        }
    }

    window.addEventListener('pagehide', (event) => {
        if (event.persisted) return;
        stopped = true;
        queue.length = 0;
        activeLoads.clear();
    });

    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            stopped = false;
            initEpisodeThumbnails();
        }
    });

    window.initEpisodeThumbnails = initEpisodeThumbnails;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => initEpisodeThumbnails(), { once: true });
    } else {
        initEpisodeThumbnails();
    }
})();

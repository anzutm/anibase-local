/* Preserve fansub ASS coordinates in the video frame, not in the page layout. */
(() => {
    'use strict';
    const assetRoot = new URL('./vendor/subtitles-octopus/', document.currentScript.src);
    const emptyTrack = 'data:text/vtt,WEBVTT%0A%0A';
    let rendererScript;

    function loadRenderer() {
        if (window.SubtitlesOctopus) return Promise.resolve();
        if (!rendererScript) {
            rendererScript = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = new URL('subtitles-octopus.js', assetRoot).href;
                script.onload = resolve;
                script.onerror = () => { script.remove(); reject(new Error('ASS renderer could not load.')); };
                document.head.appendChild(script);
            }).catch(error => { rendererScript = null; throw error; });
        }
        return rendererScript;
    }

    class AniBaseSubtitles {
        constructor(video, player) {
            this.video = video;
            this.player = player;
            this.generation = 0;
            this.renderer = null;
            this.nativePresentation = false;
            this.sync = () => this.syncVisibility();
            this.enterNative = () => {
                this.nativePresentation = true;
                if (this.renderer) {
                    this.useNativeTrack();
                    this.showNotice('Native fullscreen / PiP uses basic subtitles. For fansub positioning, use the in-page player.');
                }
                this.syncVisibility();
            };
            this.leaveNative = () => {
                this.nativePresentation = false;
                if (this.renderer) this.showNotice('');
                this.syncVisibility();
            };
            player.on('captionsenabled captionsdisabled', this.sync);
            video.addEventListener('enterpictureinpicture', this.enterNative);
            video.addEventListener('leavepictureinpicture', this.leaveNative);
            video.addEventListener('webkitbeginfullscreen', this.enterNative);
            video.addEventListener('webkitendfullscreen', this.leaveNative);
            this.onDestroy = () => this.destroy();
            player.on('destroyed', this.onDestroy);
            const track = video.querySelector('track');
            if (track) this.load(track.dataset.subtitleSrc || track.src);
        }

        showNotice(message) {
            if (!this.notice && message) {
                this.notice = document.createElement('p');
                this.notice.className = 'subtitle-renderer-notice';
                this.notice.setAttribute('role', 'status');
                this.player.elements.container.after(this.notice);
            }
            if (this.notice) {
                this.notice.textContent = message;
                this.notice.hidden = !message;
            }
        }

        releaseRenderer() {
            clearTimeout(this.readyTimer);
            const renderer = this.renderer;
            this.renderer = null;
            if (renderer) {
                // Octopus disposes itself on critical worker errors.
                if (renderer.worker) renderer.dispose();
                else renderer.canvasParent?.remove();
            }
            this.player.elements.container.classList.remove('anibase-ass-active');
        }

        useNativeTrack() {
            const track = this.video.querySelector('track');
            if (track && track.getAttribute('src') !== this.source) track.src = this.source;
        }

        syncVisibility() {
            const active = Boolean(this.renderer && !this.nativePresentation);
            const enabled = Boolean(this.player.captions.active);
            this.player.elements.container.classList.toggle('anibase-ass-active', active);
            if (this.renderer?.canvasParent) {
                this.renderer.canvasParent.style.visibility = active && enabled ? 'visible' : 'hidden';
            }
            if (this.nativePresentation || active) {
                for (const track of this.video.textTracks) {
                    track.mode = this.nativePresentation && enabled ? 'showing' : 'hidden';
                }
            }
        }

        async load(source) {
            const generation = ++this.generation;
            this.request?.abort();
            this.request = new AbortController();
            const request = this.request;
            const signal = request.signal;
            this.releaseRenderer();
            this.source = source;
            this.showNotice('');
            this.video.dataset.subtitleRenderer = 'loading';
            const track = this.video.querySelector('track');
            if (track) track.src = emptyTrack;
            const current = () => generation === this.generation;
            const fallback = (warn = false) => {
                if (!current()) return;
                this.releaseRenderer();
                this.useNativeTrack();
                this.video.dataset.subtitleRenderer = 'vtt';
                if (warn) this.showNotice('Using basic subtitles: original fansub positioning is unavailable. Try reopening the player.');
            };
            const timeout = window.setTimeout(() => { request.abort(); fallback(true); }, 100000);
            try {
                if (!window.WebAssembly || !window.Worker) { fallback(true); return; }
                const url = new URL(source, location.href);
                url.searchParams.set('format', 'manifest');
                const response = await fetch(url, { signal });
                if (!response.ok) throw new Error('Subtitle manifest unavailable.');
                const manifest = await response.json();
                if (!current()) return;
                if (manifest.renderer !== 'ass') { fallback(); return; }
                const [subtitle] = await Promise.all([
                    fetch(manifest.subtitle, { signal }).then(response => {
                        if (!response.ok) throw new Error('ASS subtitle unavailable.');
                        return response.text();
                    }),
                    loadRenderer()
                ]);
                if (!current()) return;
                this.renderer = new window.SubtitlesOctopus({
                    video: this.video,
                    subContent: subtitle,
                    fonts: manifest.fonts,
                    workerUrl: new URL('subtitles-octopus-worker.js', assetRoot).href,
                    // Keep AniBase's own typography when an ASS file does not ship its font.
                    fallbackFont: new URL('InterVariable.woff2', assetRoot).href,
                    libassMemoryLimit: 64,
                    libassGlyphLimit: 16,
                    onReady: () => {
                        if (!current()) return;
                        clearTimeout(this.readyTimer);
                        this.video.dataset.subtitleRenderer = 'ass';
                        this.syncVisibility();
                    },
                    onError: () => queueMicrotask(() => fallback(true))
                });
                const renderer = this.renderer;
                const resize = renderer.resize;
                renderer.resize = (...args) => {
                    if (!renderer.worker || !renderer.video) return;
                    const position = renderer.getVideoPosition();
                    if (!args.length && renderer.canvas.width === Math.floor(position.width * renderer.pixelRatio)
                        && renderer.canvas.height === Math.floor(position.height * renderer.pixelRatio)) {
                        // Moving letterboxes need only CSS updates. Resetting the canvas at
                        // unchanged resolution erases paused subtitles without a worker redraw.
                        const offset = renderer.canvasParent.getBoundingClientRect().top
                            - renderer.video.getBoundingClientRect().top;
                        renderer.canvas.style.top = `${position.y - offset}px`;
                        renderer.canvas.style.left = `${position.x}px`;
                        return;
                    }
                    resize(...args);
                };
                this.readyTimer = window.setTimeout(() => fallback(true), 30000);
                this.syncVisibility();
                if (this.nativePresentation) this.enterNative();
            } catch (error) {
                if (current()) fallback(true);
            } finally {
                clearTimeout(timeout);
            }
        }

        destroy() {
            ++this.generation;
            this.request?.abort();
            this.releaseRenderer();
            this.notice?.remove();
            this.player.off('captionsenabled captionsdisabled', this.sync);
            this.player.off('destroyed', this.onDestroy);
            this.video.removeEventListener('enterpictureinpicture', this.enterNative);
            this.video.removeEventListener('leavepictureinpicture', this.leaveNative);
            this.video.removeEventListener('webkitbeginfullscreen', this.enterNative);
            this.video.removeEventListener('webkitendfullscreen', this.leaveNative);
        }
    }

    window.AniBaseSubtitles = AniBaseSubtitles;
})();

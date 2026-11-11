// AniBase Desktop Titlebar & Window Controller
(function() {
    function initDesktopApp() {
        if (!window.pywebview) return;

        document.body.classList.add('is-desktop-app');

        // Startup splash transition: only shown on initial cold start in this session
        const splash = document.getElementById('desktopSplash');
        if (splash) {
            const hasLoadedBefore = sessionStorage.getItem('anibase_desktop_loaded');
            if (!hasLoadedBefore) {
                document.body.classList.add('is-desktop-loading');
                setTimeout(function() {
                    splash.classList.add('fade-out');
                    setTimeout(function() {
                        document.body.classList.remove('is-desktop-loading');
                        sessionStorage.setItem('anibase_desktop_loaded', '1');
                    }, 320);
                }, 380);
            } else {
                splash.style.display = 'none';
            }
        }

        const btnMin = document.getElementById('desktopBtnMin');
        const btnMax = document.getElementById('desktopBtnMax');
        const btnClose = document.getElementById('desktopBtnClose');
        const titlebar = document.getElementById('desktopTitlebar');
        const titleEl = document.getElementById('desktopTitlebarTitle');

        // Dynamic title synchronization
        function syncTitle() {
            if (!titleEl) return;
            let pageTitle = document.title || 'AniBase';
            pageTitle = pageTitle.replace(/\s*-\s*AniBase\s*$/, '').trim();
            titleEl.textContent = pageTitle || 'AniBase';
        }
        syncTitle();

        const titleTag = document.querySelector('title');
        if (titleTag && window.MutationObserver) {
            const observer = new MutationObserver(syncTitle);
            observer.observe(titleTag, { childList: true, subtree: true, characterData: true });
        }

        if (btnMin) {
            btnMin.addEventListener('click', function(e) {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.minimize) {
                    window.pywebview.api.minimize();
                }
            });
        }

        if (btnMax) {
            btnMax.addEventListener('click', function(e) {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize) {
                    window.pywebview.api.toggle_maximize();
                }
            });
        }

        if (btnClose) {
            btnClose.addEventListener('click', function(e) {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.close) {
                    window.pywebview.api.close();
                }
            });
        }

        if (titlebar) {
            titlebar.addEventListener('dblclick', function(e) {
                if (e.target.closest('.desktop-titlebar-controls, button, a, input')) return;
                if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize) {
                    window.pywebview.api.toggle_maximize();
                }
            });

            // Suppress browser context menu on titlebar
            titlebar.addEventListener('contextmenu', function(e) {
                e.preventDefault();
            });
        }

        // Suppress default browser context menu on empty space (preserve on inputs/video)
        document.addEventListener('contextmenu', function(e) {
            const isInput = e.target.closest('input, textarea, [contenteditable="true"], .plyr, video, audio');
            if (!isInput) {
                e.preventDefault();
            }
        });

        // External link interceptor
        document.addEventListener('click', function(e) {
            const link = e.target.closest('a[href]');
            if (!link) return;
            const href = link.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;

            try {
                const targetUrl = new URL(href, window.location.href);
                if (targetUrl.hostname !== '127.0.0.1' && targetUrl.hostname !== 'localhost') {
                    e.preventDefault();
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
                        window.pywebview.api.open_external(targetUrl.href);
                    } else {
                        window.open(targetUrl.href, '_blank');
                    }
                }
            } catch (_) {}
        });

        // Desktop keyboard shortcuts: F11 Fullscreen, Ctrl+W Close/Hide to Tray
        window.addEventListener('keydown', function(e) {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'w') {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.close) {
                    window.pywebview.api.close();
                }
            } else if (e.key === 'F11') {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_fullscreen) {
                    window.pywebview.api.toggle_fullscreen();
                } else if (document.fullscreenElement) {
                    document.exitFullscreen().catch(function() {});
                } else {
                    document.documentElement.requestFullscreen().catch(function() {});
                }
            }
        });

        // Sync initial maximized & fullscreen state
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_state) {
            window.pywebview.api.get_state().then(function(state) {
                if (state) {
                    if (state.maximized) {
                        document.body.classList.add('is-maximized');
                    }
                    if (state.fullscreen) {
                        document.body.classList.add('is-desktop-fullscreen');
                    }
                }
            }).catch(function() {});
        }
    }

    // Expose hooks for python to notify window state changes
    window.__anibase_set_maximized = function(isMax) {
        document.body.classList.toggle('is-maximized', Boolean(isMax));
    };

    window.__anibase_set_fullscreen = function(isFull) {
        document.body.classList.toggle('is-desktop-fullscreen', Boolean(isFull));
    };

    if (window.pywebview) {
        initDesktopApp();
    } else {
        window.addEventListener('pywebviewready', initDesktopApp);
    }
})();

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

        const controls = document.querySelector('.desktop-titlebar-controls');
        if (controls) {
            controls.addEventListener('mousedown', function(e) {
                e.stopPropagation();
            });
        }

        if (btnMin) {
            btnMin.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.minimize) {
                    window.pywebview.api.minimize();
                }
            });
            btnMin.addEventListener('mousedown', function(e) {
                e.stopPropagation();
            });
        }

        if (btnMax) {
            btnMax.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize) {
                    window.pywebview.api.toggle_maximize();
                }
            });
            btnMax.addEventListener('mousedown', function(e) {
                e.stopPropagation();
            });
        }

        if (btnClose) {
            btnClose.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.close) {
                    window.pywebview.api.close();
                }
            });
            btnClose.addEventListener('mousedown', function(e) {
                e.stopPropagation();
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

        // Desktop Zoom Controller
        const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
        let currentZoom = 1.0;
        let zoomToastTimer = null;

        function getZoomToast() {
            let toast = document.getElementById('desktopZoomToast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'desktopZoomToast';
                toast.className = 'desktop-zoom-toast';
                document.body.appendChild(toast);
            }
            return toast;
        }

        function showZoomToast(val) {
            const toast = getZoomToast();
            toast.textContent = Math.round(val * 100) + '%';
            toast.classList.add('show');
            if (zoomToastTimer) clearTimeout(zoomToastTimer);
            zoomToastTimer = setTimeout(function() {
                toast.classList.remove('show');
            }, 1200);
        }

        function applyZoom(factor, showToast) {
            factor = Math.max(0.5, Math.min(3.0, Math.round(factor * 100) / 100));
            currentZoom = factor;
            try {
                localStorage.setItem('anibase_desktop_zoom', String(factor));
            } catch (_) {}

            if (window.pywebview && window.pywebview.api && window.pywebview.api.set_zoom) {
                window.pywebview.api.set_zoom(factor).then(function(res) {
                    if (res && res.native) {
                        document.documentElement.style.zoom = '';
                    } else {
                        document.documentElement.style.zoom = factor === 1.0 ? '' : factor;
                    }
                }).catch(function() {
                    document.documentElement.style.zoom = factor === 1.0 ? '' : factor;
                });
            } else {
                document.documentElement.style.zoom = factor === 1.0 ? '' : factor;
            }

            if (showToast) {
                showZoomToast(factor);
            }
        }

        function zoomIn() {
            let next = ZOOM_STEPS.find(function(s) { return s > currentZoom + 0.01; });
            if (!next) next = ZOOM_STEPS[ZOOM_STEPS.length - 1];
            applyZoom(next, true);
        }

        function zoomOut() {
            let prev = ZOOM_STEPS.slice().reverse().find(function(s) { return s < currentZoom - 0.01; });
            if (!prev) prev = ZOOM_STEPS[0];
            applyZoom(prev, true);
        }

        function zoomReset() {
            applyZoom(1.0, true);
        }

        window.__anibase_zoom_in = zoomIn;
        window.__anibase_zoom_out = zoomOut;
        window.__anibase_zoom_reset = zoomReset;
        window.__anibase_set_zoom = applyZoom;

        // Restore initial zoom from localStorage
        try {
            const savedZoom = parseFloat(localStorage.getItem('anibase_desktop_zoom'));
            if (!isNaN(savedZoom) && savedZoom >= 0.5 && savedZoom <= 3.0 && savedZoom !== 1.0) {
                applyZoom(savedZoom, false);
            }
        } catch (_) {}

        // Desktop keyboard shortcuts: Zoom, F11 Fullscreen, Ctrl+W Close/Hide to Tray
        window.addEventListener('keydown', function(e) {
            if (e.ctrlKey || e.metaKey) {
                if (e.key === '=' || e.key === '+' || e.code === 'NumpadAdd') {
                    e.preventDefault();
                    zoomIn();
                    return;
                } else if (e.key === '-' || e.key === '_' || e.code === 'NumpadSubtract') {
                    e.preventDefault();
                    zoomOut();
                    return;
                } else if (e.key === '0' || e.code === 'Numpad0') {
                    e.preventDefault();
                    zoomReset();
                    return;
                } else if (e.key.toLowerCase() === 'p') {
                    e.preventDefault();
                    return;
                } else if (e.key.toLowerCase() === 'w') {
                    e.preventDefault();
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.close) {
                        window.pywebview.api.close();
                    }
                    return;
                }
            }
            if (e.key === 'F11') {
                e.preventDefault();
                if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_fullscreen) {
                    window.pywebview.api.toggle_fullscreen();
                } else if (document.fullscreenElement) {
                    document.exitFullscreen().catch(function() {});
                } else {
                    document.documentElement.requestFullscreen().catch(function() {});
                }
            }
        }, { capture: true });

        // Ctrl + Mouse Wheel for instant zoom
        window.addEventListener('wheel', function(e) {
            if (e.ctrlKey) {
                e.preventDefault();
                if (e.deltaY < 0) {
                    zoomIn();
                } else if (e.deltaY > 0) {
                    zoomOut();
                }
            }
        }, { passive: false });

        // Universal HTML5 fullscreenchange sync
        document.addEventListener('fullscreenchange', function() {
            const isFull = Boolean(document.fullscreenElement);
            document.body.classList.toggle('is-desktop-fullscreen', isFull);
            if (window.pywebview && window.pywebview.api && window.pywebview.api.set_fullscreen) {
                window.pywebview.api.set_fullscreen(isFull);
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
                    if (state.zoom && state.zoom !== 1.0 && !localStorage.getItem('anibase_desktop_zoom')) {
                        applyZoom(state.zoom, false);
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

    let isInitialized = false;
    function safeInit() {
        if (isInitialized) return;
        isInitialized = true;
        initDesktopApp();
    }

    if (window.pywebview && window.pywebview.api) {
        safeInit();
    } else {
        window.addEventListener('pywebviewready', safeInit);
        window.addEventListener('DOMContentLoaded', function() {
            setTimeout(function() {
                if (window.pywebview) {
                    safeInit();
                }
            }, 100);
        });
    }
})();

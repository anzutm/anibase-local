(() => {
    const root = document.getElementById('setupPreparation');
    if (!root) return;
    const label = document.getElementById('setupProgressLabel');
    const copy = document.getElementById('setupLoadingText');
    const bar = document.getElementById('setupProgressBar');
    const percent = document.getElementById('setupProgressPercent');
    const fill = document.getElementById('setupProgressFill');
    const count = document.getElementById('setupProgressCount');
    const state = document.getElementById('setupState');
    const actions = document.getElementById('setupActions');
    const stages = ['scan', 'metadata', 'complete'];
    let running = false;
    function render(data) {
        const current = Math.max(0, Number(data.current) || 0);
        const total = Math.max(0, Number(data.total) || 0);
        const stage = stages.includes(data.stage) ? data.stage : 'scan';
        const step = stages.indexOf(stage);
        document.querySelectorAll('.prep-steps li').forEach((item, index) => {
            item.classList.toggle('is-done', index < step || data.done === true);
            if (index === step) item.setAttribute('aria-current', 'step');
            else item.removeAttribute('aria-current');
        });
        label.textContent = ['Finding your collection', 'Bringing the details together', 'Your library is ready'][step];
        copy.textContent = ['Reading your folders and adding anime to the library.', 'Matching titles and collecting artwork and anime information.', 'Opening your library. Enjoy your next escape.'][step];
        const progress = stage === 'complete' ? 100 : total > 0 ? Math.round((step === 0 ? 0 : 45) + Math.min(1, current / total) * (step === 0 ? 45 : 50)) : null;
        if (progress === null) bar.removeAttribute('aria-valuenow');
        else bar.setAttribute('aria-valuenow', String(progress));
        percent.textContent = progress === null ? '—' : `${progress}%`;
        fill.style.width = progress === null ? '' : `${progress}%`;
        count.textContent = stage === 'complete' ? `${Number(data.anime_count) || 0} anime found` : total > 0 ? `${current} / ${total} folders` : 'Discovering folders';
        if (stage === 'complete') state.textContent = 'Ready';
    }
    async function request(method) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 60000);
        try {
            const response = await fetch(root.dataset.syncUrl, { method, cache: 'no-store', signal: controller.signal, headers: { 'X-AniBase-Action-Token': window.ANIBASE_ACTION_TOKEN || '' } });
            const data = await response.json();
            if (!response.ok || !data.ok || data.stage === 'error') throw new Error(data.message || 'Scan unavailable');
            return data;
        } finally { clearTimeout(timeout); }
    }
    async function start() {
        if (running) return;
        running = true;
        actions.hidden = true;
        root.removeAttribute('data-error');
        state.textContent = 'In progress';
        render({ stage: 'scan' });
        try {
            let data = await request('POST');
            while (!data.done) {
                render(data);
                await new Promise(resolve => setTimeout(resolve, 750));
                data = await request('GET');
            }
            render({ ...data, stage: 'complete' });
            if (!Number(data.anime_count)) copy.textContent = 'No anime folders found yet. You can adjust your paths in Settings.';
            await new Promise(resolve => setTimeout(resolve, 1200));
            window.location.replace(root.dataset.homeUrl);
        } catch (error) {
            root.setAttribute('data-error', 'true');
            state.textContent = 'Needs attention';
            label.textContent = "We couldn't finish the scan";
            copy.textContent = 'Your settings are saved. Check your folder connection and try again, or open the library to continue.';
            if (!bar.hasAttribute('aria-valuenow')) { bar.setAttribute('aria-valuenow', '0'); fill.style.width = '0%'; }
            actions.hidden = false;
        } finally { running = false; }
    }
    document.getElementById('setupRetry').addEventListener('click', start);
    start();
})();

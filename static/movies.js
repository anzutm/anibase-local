(function () {
    const searchInputs = [document.getElementById('animeSearch'), document.getElementById('moviesSearch')].filter(Boolean);
    const hero = document.querySelector('.movies-hero');
    const slides = Array.from(document.querySelectorAll('[data-movies-hero-slide]'));
    const dots = Array.from(document.querySelectorAll('[data-movies-hero-dot]'));
    const pauseButton = document.getElementById('moviesHeroPause');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let activeIndex = 0;
    let paused = motion.matches;
    let timer;

    function stopSlider() { window.clearInterval(timer); }
    function startSlider() {
        stopSlider();
        if (paused || document.hidden || hero?.hidden || hero?.matches(':hover') || hero?.contains(document.activeElement) || slides.length < 2) return;
        timer = window.setInterval(() => showSlide(activeIndex + 1), 4500);
    }
    function showSlide(index) {
        activeIndex = index % slides.length;
        slides.forEach((slide, i) => {
            slide.classList.toggle('active', i === activeIndex);
            slide.setAttribute('aria-hidden', String(i !== activeIndex));
            slide.inert = i !== activeIndex;
        });
        dots.forEach((dot, i) => {
            dot.classList.toggle('active', i === activeIndex);
            dot.setAttribute('aria-pressed', String(i === activeIndex));
        });
    }
    function updatePauseButton() {
        if (!pauseButton) return;
        pauseButton.textContent = paused ? 'Play slideshow' : 'Pause slideshow';
        pauseButton.setAttribute('aria-pressed', String(paused));
    }
    dots.forEach((dot, i) => dot.addEventListener('click', () => { showSlide(i); startSlider(); }));
    pauseButton?.addEventListener('click', () => { paused = !paused; updatePauseButton(); startSlider(); });
    hero?.addEventListener('mouseenter', stopSlider);
    hero?.addEventListener('mouseleave', startSlider);
    hero?.addEventListener('focusin', stopSlider);
    hero?.addEventListener('focusout', () => window.setTimeout(startSlider, 0));
    document.addEventListener('visibilitychange', startSlider);
    motion.addEventListener('change', () => { paused = motion.matches; updatePauseButton(); startSlider(); });
    updatePauseButton();
    startSlider();

    const grid = document.getElementById('moviesGrid');
    const cards = Array.from(document.querySelectorAll('[data-movie-title]'));
    const filters = Array.from(document.querySelectorAll('[data-movie-filter]'));
    const sort = document.getElementById('moviesSort');
    const count = document.getElementById('moviesResultCount');
    const empty = document.getElementById('moviesSearchEmpty');
    let status = 'all';
    let term = '';
    let sortMode = sort?.value || 'default';
    const libraryOrder = new Map(cards.map((card, index) => [card, index]));
    const sortLabels = {
        default: 'Library order',
        title: 'Title A–Z',
        year: 'Newest release',
        score: 'Highest rated'
    };

    function compareTitles(a, b) {
        return a.dataset.movieTitle.localeCompare(b.dataset.movieTitle, undefined, {
            sensitivity: 'base',
            numeric: true
        });
    }

    function applySort() {
        if (!grid) return;

        const ordered = [...cards];
        if (sortMode === 'title') {
            ordered.sort(compareTitles);
        } else if (sortMode === 'year' || sortMode === 'score') {
            const key = sortMode === 'year' ? 'movieYear' : 'movieScore';
            ordered.sort((a, b) => {
                const difference = (Number(b.dataset[key]) || 0) - (Number(a.dataset[key]) || 0);
                return difference || compareTitles(a, b);
            });
        } else {
            ordered.sort((a, b) => libraryOrder.get(a) - libraryOrder.get(b));
        }
        ordered.forEach(card => grid.appendChild(card));
    }

    function updateResults() {
        let visible = 0;
        cards.forEach(card => {
            card.hidden = !(card.dataset.movieTitle.includes(term) && (status === 'all' || card.dataset.movieStatus === status));
            if (!card.hidden) visible++;
        });
        if (count) {
            const sortFeedback = sortMode === 'default' ? '' : ` · ${sortLabels[sortMode]}`;
            count.textContent = `${visible} of ${cards.length} movies${sortFeedback}`;
        }
        if (empty) empty.hidden = visible > 0;
        if (hero) hero.hidden = Boolean(term) || status !== 'all';
        filters.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.movieFilter === status)));
        startSlider();
    }
    searchInputs.forEach(input => input.addEventListener('input', () => {
        searchInputs.forEach(other => { other.value = input.value; });
        term = input.value.toLowerCase().trim();
        updateResults();
    }));
    filters.forEach(button => button.addEventListener('click', () => { status = button.dataset.movieFilter; updateResults(); }));
    sort?.addEventListener('change', () => {
        sortMode = sort.value;
        applySort();
        updateResults();
    });
    document.getElementById('moviesReset')?.addEventListener('click', () => {
        term = ''; status = 'all'; sortMode = 'default';
        if (sort) sort.value = sortMode;
        searchInputs.forEach(input => { input.value = ''; });
        applySort();
        updateResults();
        document.getElementById('moviesSearch')?.focus();
    });
    if (cards.length) {
        term = (searchInputs[0]?.value || '').toLowerCase().trim();
        updateResults();
    }
})();

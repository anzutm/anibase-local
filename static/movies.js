(function () {
    const searchInput = document.getElementById('animeSearch');
    const hero = document.querySelector('.movies-hero');
    const cards = Array.from(document.querySelectorAll('.movie-poster-card[data-movie-title]'));
    const resultCount = document.getElementById('moviesResultCount');
    const emptyState = document.getElementById('moviesSearchEmpty');
    const heroSlides = Array.from(document.querySelectorAll('[data-movies-hero-slide]'));
    const heroDots = Array.from(document.querySelectorAll('[data-movies-hero-dot]'));
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let activeHeroIndex = 0;
    let heroTimer;

    function showHeroSlide(index) {
        if (!heroSlides.length) return;

        const nextIndex = (index + heroSlides.length) % heroSlides.length;
        heroSlides.forEach((slide, slideIndex) => {
            const isActive = slideIndex === nextIndex;
            slide.classList.toggle('active', isActive);
            slide.setAttribute('aria-hidden', String(!isActive));
        });
        heroDots.forEach((dot, dotIndex) => {
            const isActive = dotIndex === nextIndex;
            dot.classList.toggle('active', isActive);
            dot.setAttribute('aria-pressed', String(isActive));
        });
        activeHeroIndex = nextIndex;
    }

    function stopHeroSlider() {
        window.clearInterval(heroTimer);
        heroTimer = undefined;
    }

    function startHeroSlider() {
        stopHeroSlider();
        if (heroSlides.length < 2 || reduceMotion || document.hidden) return;
        heroTimer = window.setInterval(() => showHeroSlide(activeHeroIndex + 1), 3000);
    }

    if (heroSlides.length > 1) {
        heroDots.forEach((dot, index) => {
            dot.addEventListener('click', () => {
                showHeroSlide(index);
                startHeroSlider();
            });
        });
        hero?.addEventListener('mouseenter', stopHeroSlider);
        hero?.addEventListener('mouseleave', startHeroSlider);
        document.addEventListener('visibilitychange', startHeroSlider);
        startHeroSlider();
    }

    if (!searchInput || !cards.length) return;

    function updateMovieResults() {
        const term = searchInput.value.toLowerCase().trim();
        let visibleCount = 0;

        cards.forEach((card) => {
            const isVisible = !term || card.dataset.movieTitle.includes(term);
            card.hidden = !isVisible;
            if (isVisible) visibleCount += 1;
        });

        if (hero) hero.hidden = Boolean(term);
        if (resultCount) {
            resultCount.textContent = `${visibleCount} movie${visibleCount === 1 ? '' : 's'}`;
        }
        if (emptyState) emptyState.hidden = visibleCount > 0;
    }

    searchInput.addEventListener('input', updateMovieResults);
})();

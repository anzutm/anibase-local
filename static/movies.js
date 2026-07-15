(function () {
    const searchInput = document.getElementById('animeSearch');
    const hero = document.querySelector('.movies-hero');
    const cards = Array.from(document.querySelectorAll('.movie-poster-card[data-movie-title]'));
    const resultCount = document.getElementById('moviesResultCount');
    const emptyState = document.getElementById('moviesSearchEmpty');

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

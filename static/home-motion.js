/* Home motion: progressive enhancement; content stays readable without JavaScript. */
document.addEventListener('DOMContentLoaded', () => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const hover = matchMedia('(hover: hover) and (pointer: fine)');
    const animations = new Map();
    function reveal(element, delay = 0) {
        animations.get(element)?.cancel();
        if (!element || reduced.matches || document.hidden || !element.animate) return;
        const animation = element.animate([
            { opacity: 0, translate: '0 14px' },
            { opacity: 1, translate: '0 0' }
        ], { duration: 520, delay, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' });
        animations.set(element, animation);
        animation.onfinish = animation.oncancel = () => {
            if (animations.get(element) === animation) animations.delete(element);
        };
    }
    document.querySelectorAll('.home-intro .home-section-eyebrow, .home-intro h1, .home-collection-summary, .home-spotlight')
        .forEach((element, index) => reveal(element, index * 65));

    const targets = document.querySelectorAll('.resume-section-head, .collection-top, .collection-toolbar');
    const seen = new WeakSet();
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver(entries => {
            let index = 0;
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                if (!seen.has(entry.target)) reveal(entry.target, Math.min(index++ * 45, 180));
                seen.add(entry.target);
            });
        }, { threshold: .08 });
        targets.forEach(element => observer.observe(element));
    }
    document.addEventListener('focusin', event => {
        for (const [element, animation] of animations) {
            if (element.contains(event.target)) animation.cancel();
        }
    });

    const hero = document.querySelector('.home-spotlight');
    const slides = [...document.querySelectorAll('.home-spotlight-slide')];
    const dots = [...document.querySelectorAll('.home-spotlight-pagination .dot')];
    let index = Math.max(0, slides.findIndex(slide => slide.classList.contains('active')));
    let elapsed = 0;
    let lastTime = null;
    let frame = null;
    let imageMotion = null;
    let hovered = false;
    let inView = true;
    const duration = 5000;

    function paint() {
        dots[index]?.style.setProperty('--slide-progress', String(elapsed / duration));
        if (imageMotion) imageMotion.currentTime = elapsed;
    }
    function prepareImage() {
        imageMotion?.cancel();
        imageMotion = null;
        const image = slides[index]?.querySelector('.home-spotlight-image');
        if (image?.animate && slides.length > 1 && !reduced.matches) {
            imageMotion = image.animate([{ transform: 'scale(1.01)' }, { transform: 'scale(1.05)' }],
                { duration, fill: 'both', easing: 'linear' });
            imageMotion.pause();
        }
        paint();
    }
    function showSlide(next) {
        if (next === index) return;
        slides[index].classList.remove('active');
        slides[index].setAttribute('aria-hidden', 'true');
        slides[index].inert = true;
        dots[index]?.classList.remove('active');
        dots[index]?.setAttribute('aria-pressed', 'false');
        dots[index]?.style.removeProperty('--slide-progress');
        index = next;
        elapsed = 0;
        slides[index].classList.add('active');
        slides[index].setAttribute('aria-hidden', 'false');
        slides[index].inert = false;
        dots[index]?.classList.add('active');
        dots[index]?.setAttribute('aria-pressed', 'true');
        prepareImage();
    }
    function canRun() {
        return slides.length > 1 && !reduced.matches && !document.hidden && inView
            && !hovered && !hero.contains(document.activeElement)
            && hero.style.display !== 'none';
    }
    function tick(time) {
        frame = null;
        if (!canRun()) { lastTime = null; return; }
        if (lastTime !== null) elapsed += time - lastTime;
        lastTime = time;
        if (elapsed >= duration) showSlide((index + 1) % slides.length);
        paint();
        frame = requestAnimationFrame(tick);
    }
    function sync() {
        if (frame !== null) cancelAnimationFrame(frame);
        frame = null;
        lastTime = null;
        if (canRun()) frame = requestAnimationFrame(tick);
    }
    if (hero && slides.length > 1) {
        hero.classList.add('home-motion-ready');
        dots.forEach((dot, next) => dot.addEventListener('click', () => { showSlide(next); sync(); }));
        hero.addEventListener('pointerenter', () => { hovered = hover.matches; sync(); });
        hero.addEventListener('pointerleave', () => { hovered = false; sync(); });
        hero.addEventListener('focusin', sync);
        hero.addEventListener('focusout', () => queueMicrotask(sync));
        if ('IntersectionObserver' in window) {
            new IntersectionObserver(entries => { inView = entries[0].isIntersecting; sync(); }).observe(hero);
        }
        prepareImage();
        sync();
    }
    document.addEventListener('home:filtered', sync);
    document.addEventListener('visibilitychange', () => {
        animations.forEach(animation => document.hidden ? animation.pause() : animation.play());
        sync();
    });
    reduced.addEventListener('change', () => {
        if (reduced.matches) animations.forEach(animation => animation.cancel());
        prepareImage();
        sync();
    });
    window.addEventListener('pagehide', () => { cancelAnimationFrame(frame); frame = null; lastTime = null; });
    window.addEventListener('pageshow', sync);
});

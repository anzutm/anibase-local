document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('settingsPreferencesForm');
    if (!form) return;
    const layout = form.querySelector('.settings-layout');
    ['library', 'appearance', 'playback', 'integrations', 'automation', 'maintenance'].forEach(name => {
        layout.appendChild(document.getElementById(`settings-${name}`));
    });
    layout.querySelector('.settings-main-column').remove();
    layout.querySelector('.settings-side').remove();
    const backup = document.getElementById('settings-backup');
    form.after(backup);

    const links = [...document.querySelectorAll('.settings-nav a')];
    function highlight(id) {
        links.forEach(link => {
            if (link.hash === `#${id}`) link.setAttribute('aria-current', 'location');
            else link.removeAttribute('aria-current');
        });
    }
    highlight('settings-library');
    const observer = new IntersectionObserver(entries => {
        const visible = entries.filter(entry => entry.isIntersecting);
        if (visible.length) highlight(visible[0].target.id);
    }, { rootMargin: '-15% 0px -55% 0px', threshold: 0 });
    document.querySelectorAll('.settings-card[id]').forEach(panel => observer.observe(panel));
    links.forEach(link => link.addEventListener('click', () => highlight(link.hash.slice(1))));

    const status = document.getElementById('settingsSaveStatus');
    const discard = document.getElementById('settingsDiscard');
    const initialTheme = document.documentElement.dataset.theme;
    const snapshot = () => JSON.stringify([...new FormData(form)].filter(([name]) => name !== 'action_token'));
    const baseline = snapshot();
    const originalPaths = form.querySelector('#libraryPathList').innerHTML;
    const destination = form.querySelector('#auto_import_destination_root');
    const originalDestinations = destination.innerHTML;
    const originalDestination = destination.value;
    const mappings = form.querySelector('#autoImportMappingList');
    const originalMappings = mappings?.innerHTML;
    let dirty = false;
    function update() {
        dirty = snapshot() !== baseline;
        status.textContent = dirty ? 'You have unsaved changes.' : 'Your preferences are up to date.';
        discard.hidden = !dirty;
        form.classList.toggle('has-changes', dirty);
    }
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    new MutationObserver(update).observe(layout, { childList: true, subtree: true });
    discard.addEventListener('click', () => {
        form.reset();
        form.querySelector('#libraryPathList').innerHTML = originalPaths;
        destination.innerHTML = originalDestinations;
        destination.value = originalDestination;
        if (mappings) mappings.innerHTML = originalMappings;
        document.documentElement.dataset.theme = initialTheme;
        update();
    });
    // Native POST handlers remain responsible for saving and one-off actions.
    document.querySelectorAll('.settings-page form').forEach(item => {
        item.addEventListener('submit', () => { dirty = false; });
    });
    window.addEventListener('beforeunload', event => {
        if (dirty) { event.preventDefault(); event.returnValue = ''; }
    });
    update();
});

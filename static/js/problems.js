(function () {
    function resultButton(item, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'list-group-item list-group-item-action';
        button.textContent = item.name;
        button.addEventListener('click', () => onClick(item));
        return button;
    }

    function search(input, results, type, onSelect) {
        let timer;
        input.addEventListener('input', () => {
            clearTimeout(timer);
            const query = input.value.trim();
            if (query.length < 2) {
                results.classList.add('d-none');
                return;
            }
            timer = setTimeout(async () => {
                try {
                    const response = await fetch(`/problems/lookup/${type}?q=${encodeURIComponent(query)}`);
                    const items = await response.json();
                    results.innerHTML = '';
                    if (!items.length) {
                        const empty = document.createElement('div');
                        empty.className = 'list-group-item text-muted';
                        empty.textContent = 'No results found';
                        results.appendChild(empty);
                    } else {
                        items.forEach(item => results.appendChild(resultButton(item, selected => {
                            onSelect(selected);
                            results.classList.add('d-none');
                        })));
                    }
                    results.classList.remove('d-none');
                } catch (error) {
                    results.classList.add('d-none');
                }
            }, 220);
        });
        document.addEventListener('click', event => {
            if (!results.contains(event.target) && event.target !== input) results.classList.add('d-none');
        });
    }

    function initSingle(root) {
        const input = root.querySelector('[data-lookup-input]');
        const hidden = root.querySelector('[data-lookup-value]');
        const results = root.querySelector('[data-lookup-results]');
        const type = root.dataset.lookupType;
        if (!input || !hidden || !results || !type) return;
        input.addEventListener('input', () => { hidden.value = ''; });
        search(input, results, type, item => {
            input.value = item.name;
            hidden.value = item.id;
            hidden.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }

    function initMulti(root) {
        const input = root.querySelector('[data-lookup-input]');
        const results = root.querySelector('[data-lookup-results]');
        const chips = root.querySelector('[data-selected-chips]');
        const type = root.dataset.lookupType;
        const field = root.dataset.fieldName;
        if (!input || !results || !chips || !type || !field) return;

        const add = item => {
            if (chips.querySelector(`[data-id="${item.id}"]`)) return;
            const chip = document.createElement('span');
            chip.className = 'problem-selected-chip';
            chip.dataset.id = item.id;
            const name = document.createElement('span');
            name.textContent = item.name;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.setAttribute('aria-label', 'Remove');
            remove.innerHTML = '&times;';
            remove.addEventListener('click', () => chip.remove());
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = field;
            hidden.value = item.id;
            chip.append(name, remove, hidden);
            chips.appendChild(chip);
            input.value = '';
        };
        chips.querySelectorAll('.problem-selected-chip').forEach(chip => {
            chip.querySelector('button')?.addEventListener('click', () => chip.remove());
        });
        search(input, results, type, add);
    }

    function initCause(root) {
        const category = root.querySelector('[data-cause-category]');
        const hidden = root.querySelector('[data-cause-object]');
        const userSelect = root.querySelector('[data-cause-user]');
        const partySections = root.querySelectorAll('[data-cause-party]');
        const typeRoot = root.querySelector('[data-problem-type-lookup]');
        const typeInput = typeRoot?.querySelector('[data-problem-type-name]');
        const typeId = typeRoot?.querySelector('[data-problem-type-id]');
        const typeResults = typeRoot?.querySelector('[data-problem-type-results]');
        const typeHelp = typeRoot?.querySelector('[data-problem-type-help]');
        if (!category || !hidden) return;

        let typeTimer;
        const hideTypeResults = () => typeResults?.classList.add('d-none');
        const updateTypeAvailability = (clear) => {
            if (!typeInput) return;
            const hasCause = Boolean(category.value);
            typeInput.disabled = !hasCause;
            typeInput.placeholder = hasCause ? 'Search or enter a type...' : 'Choose a cause first';
            if (typeHelp) typeHelp.textContent = hasCause
                ? 'Choose an existing type for this cause, or enter a new one.'
                : 'Problem types are specific to the selected cause.';
            if (clear) {
                typeInput.value = '';
                if (typeId) typeId.value = '';
            }
            hideTypeResults();
        };

        const update = (preserve) => {
            const party = category.options[category.selectedIndex]?.dataset.party || '';
            partySections.forEach(section => {
                section.classList.toggle('d-none', section.dataset.causeParty !== party);
            });
            if (!preserve) {
                hidden.value = '';
                if (userSelect) userSelect.value = '';
                root.querySelectorAll('[data-cause-search]').forEach(input => { input.value = ''; });
            }
        };
        category.addEventListener('change', () => {
            update(false);
            updateTypeAvailability(true);
        });
        userSelect?.addEventListener('change', () => { hidden.value = userSelect.value; });
        root.querySelectorAll('.problem-cause-lookup').forEach(section => {
            const type = section.dataset.causeParty;
            const input = section.querySelector('[data-cause-search]');
            const results = section.querySelector('[data-lookup-results]');
            if (!input || !results) return;
            input.addEventListener('input', () => { hidden.value = ''; });
            search(input, results, type, item => {
                input.value = item.name;
                hidden.value = item.id;
            });
        });
        typeInput?.addEventListener('input', () => {
            if (typeId) typeId.value = '';
            clearTimeout(typeTimer);
            const query = typeInput.value.trim();
            if (!category.value) {
                hideTypeResults();
                return;
            }
            typeTimer = setTimeout(async () => {
                try {
                    const url = `/problems/types/search?cause_category_id=${encodeURIComponent(category.value)}&q=${encodeURIComponent(query)}`;
                    const response = await fetch(url);
                    const items = await response.json();
                    typeResults.innerHTML = '';
                    items.forEach(item => typeResults.appendChild(resultButton(item, selected => {
                        typeInput.value = selected.name;
                        typeId.value = selected.id;
                        hideTypeResults();
                    })));
                    if (!items.length && query) {
                        const create = document.createElement('div');
                        create.className = 'list-group-item text-muted';
                        create.textContent = `Press Create problem to add “${query}” to this cause`;
                        typeResults.appendChild(create);
                    }
                    typeResults.classList.toggle('d-none', !query && !items.length);
                } catch (error) {
                    hideTypeResults();
                }
            }, 180);
        });
        typeInput?.addEventListener('focus', () => {
            if (category.value) typeInput.dispatchEvent(new Event('input'));
        });
        document.addEventListener('click', event => {
            if (typeRoot && !typeRoot.contains(event.target)) hideTypeResults();
        });
        update(true);
        updateTypeAvailability(false);
    }

    document.querySelectorAll('.problem-single-lookup').forEach(initSingle);
    document.querySelectorAll('.problem-multi-lookup').forEach(initMulti);
    document.querySelectorAll('.problem-form').forEach(initCause);
})();

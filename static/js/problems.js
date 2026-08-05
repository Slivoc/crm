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
        if (!category || !hidden) return;

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
        category.addEventListener('change', () => update(false));
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
        update(true);
    }

    document.querySelectorAll('.problem-single-lookup').forEach(initSingle);
    document.querySelectorAll('.problem-multi-lookup').forEach(initMulti);
    document.querySelectorAll('.problem-form').forEach(initCause);
})();

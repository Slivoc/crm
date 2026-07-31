(() => {
    const money = new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP', maximumFractionDigits: 0});
    let data = window.TV_DASHBOARD_DATA;
    let newsIndex = 0;
    let extendedTimer;
    const el = id => document.getElementById(id);
    const text = (id, value) => { if (el(id)) el(id).textContent = value; };

    function fitDashboard() {
        const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
        el('tvDashboard').style.setProperty('--tv-scale', scale);
    }

    function renderNews() {
        const items = data.news || [];
        if (!items.length) {
            text('newsTitle', 'No recent customer news — awaiting the next news scan');
            text('newsMeta', 'Customer intelligence');
            return;
        }
        const item = items[newsIndex % items.length];
        text('newsTitle', item.title);
        text('newsMeta', [item.customer_names, item.source_name].filter(Boolean).join(' · '));
        [...el('newsDots').children].forEach((dot, index) => dot.classList.toggle('active', index === newsIndex % items.length));
    }

    function render(snapshot) {
        data = snapshot;
        text('monthLabel', snapshot.month_label);
        text('updatedAt', `Updated ${new Date(snapshot.updated_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`);
        text('salesActual', money.format(snapshot.sales.actual));
        text('salesTarget', money.format(snapshot.sales.target));
        text('salesPercent', `${snapshot.sales.percentage.toFixed(1)}%`);
        text('salesRemaining', snapshot.sales.target ? `${money.format(snapshot.sales.remaining)} remaining` : 'Monthly target not set');
        text('orderCount', `${snapshot.sales.order_count} orders booked`);
        el('salesProgress').style.width = `${Math.min(snapshot.sales.percentage, 100)}%`;
        el('ordersList').innerHTML = (snapshot.biggest_orders || []).slice(0, 3).map((order, index) => `<li style="animation-delay:${index * 80}ms"><span class="order-logo">${order.logo_url ? `<img src="${escapeAttribute(order.logo_url)}" alt="">` : escapeHtml((order.customer_name || '?').charAt(0))}</span><div><span class="row-name">${customerWithSalesperson(order.customer_name, order.salesperson_name)}</span><span class="row-meta">${escapeHtml(order.sales_order_ref)}</span></div><span class="row-value">${money.format(Number(order.total_value || 0))}</span></li>`).join('') || '<li><div><span class="row-name">No orders yet this month</span></div></li>';
        el('nonDaveOrdersList').innerHTML = (snapshot.biggest_non_dave_orders || []).map((order, index) => `<li style="animation-delay:${index * 80}ms"><span class="order-logo compact">${order.logo_url ? `<img src="${escapeAttribute(order.logo_url)}" alt="">` : escapeHtml((order.customer_name || '?').charAt(0))}</span><div><span class="row-name">${customerWithSalesperson(order.customer_name, order.salesperson_name)}</span><span class="row-meta">${escapeHtml(order.sales_order_ref)}</span></div><span class="row-value">${money.format(Number(order.total_value || 0))}</span></li>`).join('') || '<li><div><span class="row-name">No non-Dave orders yet</span></div></li>';
        const topCustomers = snapshot.highest_spending_customers || [];
        el('topCustomerList').innerHTML = topCustomers.map((customer, index) => `<div class="customer-item ranked-customer"><span class="customer-rank">${index + 1}</span><span><strong>${customerWithSalesperson(customer.name, customer.salesperson_names)}</strong><span class="row-meta">${customer.order_count} order${Number(customer.order_count) === 1 ? '' : 's'}</span></span><span class="row-value">${money.format(Number(customer.month_value || 0))}</span></div>`).join('') || '<div class="customer-item">No customer spend yet this month</div>';
        text('customerCount', snapshot.new_customers.length);
        el('customerList').innerHTML = snapshot.new_customers.slice(0, 3).map(customer => `<div class="customer-item"><strong>${customerWithSalesperson(customer.name, customer.salesperson_names)}</strong><span class="row-meta">${money.format(Number(customer.month_value || 0))} this month</span></div>`).join('') || '<div class="customer-item">Awaiting the first new customer</div>';
        text('employeeName', snapshot.employee.name || 'To be announced');
        text('employeeDescription', snapshot.employee.description || 'Celebrate a team member here.');
        const photo = el('employeePhoto');
        photo.src = snapshot.employee.image_url || '';
        photo.style.display = snapshot.employee.image_url ? 'block' : 'none';
        el('employeePlaceholder').style.display = snapshot.employee.image_url ? 'none' : 'block';
        el('newsDots').innerHTML = snapshot.news.map(() => '<i></i>').join('');
        newsIndex %= Math.max(snapshot.news.length, 1);
        renderNews();
    }

    function escapeHtml(value) {
        const node = document.createElement('div');
        node.textContent = value || '';
        return node.innerHTML;
    }

    function escapeAttribute(value) {
        return escapeHtml(value).replace(/`/g, '&#96;');
    }

    async function showExtendedStory() {
        const item = (data.news || [])[newsIndex % Math.max((data.news || []).length, 1)];
        if (!item?.id || el('storyScreen').classList.contains('active')) return;
        try {
            const response = await fetch(`/dashboard/tv/news/${item.id}/extended`, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
            if (!response.ok) return;
            const story = (await response.json()).story;
            text('storyTitle', story.title); text('storySource', [story.customer_names, story.source_name].filter(Boolean).join(' · '));
            text('storySummary', story.summary); text('storyRelevance', story.commercial_angle); text('storyActions', story.suggested_action);
            el('sceneWipe').classList.add('active');
            setTimeout(() => el('storyScreen').classList.add('active'), 450);
            setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
            extendedTimer = setTimeout(hideExtendedStory, 20000);
        } catch (_) { /* Keep the live dashboard running if research is unavailable. */ }
    }

    function hideExtendedStory() {
        clearTimeout(extendedTimer);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('storyScreen').classList.remove('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
    }

    function customerWithSalesperson(customerName, salespersonName) {
        const customer = escapeHtml(customerName);
        return salespersonName
            ? `${customer} <span class="salesperson-name">· ${escapeHtml(salespersonName)}</span>`
            : customer;
    }

    async function refresh() {
        try {
            const response = await fetch(`/dashboard/tv/data?_=${Date.now()}`, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
            if (!response.ok) throw new Error('Refresh failed');
            render(await response.json());
            document.querySelector('.status-dot').style.background = '';
        } catch (error) {
            text('updatedAt', 'Data delayed');
            document.querySelector('.status-dot').style.background = '#ffc85c';
        }
    }

    fitDashboard();
    render(data);
    window.addEventListener('resize', fitDashboard);
    setInterval(() => { newsIndex += 1; renderNews(); }, 9000);
    setInterval(refresh, 15000);
    setInterval(showExtendedStory, 60000);

    const dialog = el('employeeDialog');
    if (dialog) {
        el('editEmployee').addEventListener('click', () => {
            el('employeeNameInput').value = data.employee.name || '';
            el('employeeDescriptionInput').value = data.employee.description || '';
            dialog.showModal();
        });
        el('closeEmployee').addEventListener('click', () => dialog.close());
        el('employeeForm').addEventListener('submit', async event => {
            event.preventDefault();
            text('employeeError', '');
            const response = await fetch('/dashboard/tv/employee', {method: 'POST', body: new FormData(event.target)});
            const result = await response.json();
            if (!response.ok) return text('employeeError', result.error || 'Unable to save.');
            data.employee = result.employee;
            render(data);
            dialog.close();
        });
    }
})();

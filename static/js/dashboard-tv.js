(() => {
    const money = new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP', maximumFractionDigits: 0});
    let data = window.TV_DASHBOARD_DATA;
    let newsIndex = 0;
    const el = id => document.getElementById(id);
    const text = (id, value) => { if (el(id)) el(id).textContent = value; };

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
        el('ordersList').innerHTML = (snapshot.biggest_orders || []).slice(0, 3).map((order, index) => `<li style="animation-delay:${index * 80}ms"><div><span class="row-name">${escapeHtml(order.customer_name)}</span><span class="row-meta">${escapeHtml(order.sales_order_ref)}</span></div><span class="row-value">${money.format(Number(order.total_value || 0))}</span></li>`).join('') || '<li><div><span class="row-name">No orders yet this month</span></div></li>';
        el('nonDaveOrdersList').innerHTML = (snapshot.biggest_non_dave_orders || []).map((order, index) => `<li style="animation-delay:${index * 80}ms"><div><span class="row-name">${escapeHtml(order.customer_name)}</span><span class="row-meta">${escapeHtml(order.sales_order_ref)}</span></div><span class="row-value">${money.format(Number(order.total_value || 0))}</span></li>`).join('') || '<li><div><span class="row-name">No non-Dave orders yet</span></div></li>';
        const topCustomers = snapshot.highest_spending_customers || [];
        el('topCustomerList').innerHTML = topCustomers.map((customer, index) => `<div class="customer-item ranked-customer"><span class="customer-rank">${index + 1}</span><span><strong>${escapeHtml(customer.name)}</strong><span class="row-meta">${customer.order_count} order${Number(customer.order_count) === 1 ? '' : 's'}</span></span><span class="row-value">${money.format(Number(customer.month_value || 0))}</span></div>`).join('') || '<div class="customer-item">No customer spend yet this month</div>';
        text('customerCount', snapshot.new_customers.length);
        el('customerList').innerHTML = snapshot.new_customers.slice(0, 3).map(customer => `<div class="customer-item"><strong>${escapeHtml(customer.name)}</strong><span class="row-meta">${money.format(Number(customer.month_value || 0))} this month</span></div>`).join('') || '<div class="customer-item">Awaiting the first new customer</div>';
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

    async function refresh() {
        try {
            const response = await fetch('/dashboard/tv/data', {headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error('Refresh failed');
            render(await response.json());
            document.querySelector('.status-dot').style.background = '';
        } catch (error) {
            text('updatedAt', 'Data delayed');
            document.querySelector('.status-dot').style.background = '#ffc85c';
        }
    }

    render(data);
    setInterval(() => { newsIndex += 1; renderNews(); }, 9000);
    setInterval(refresh, 60000);

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

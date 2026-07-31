(() => {
    const money = new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP', maximumFractionDigits: 0});
    let data = window.TV_DASHBOARD_DATA;
    let newsIndex = 0;
    let headlineIndex = 0;
    let extendedTimer;
    let storyScrollTimer;
    let portalTimer;
    let headlineTimer;
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

    function markdown(value) {
        const escaped = escapeHtml(value).replace(/\r\n?/g, '\n');
        const inline = line => line
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/__([^_]+)__/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
        const blocks = [];
        let listType = '';
        escaped.split('\n').forEach(rawLine => {
            const line = rawLine.trim();
            const listMatch = line.match(/^([-*•]|\d+\.)\s+(.+)$/);
            if (listMatch) {
                const type = /\d+\./.test(listMatch[1]) ? 'ol' : 'ul';
                if (listType !== type) {
                    if (listType) blocks.push(`</${listType}>`);
                    blocks.push(`<${type}>`);
                    listType = type;
                }
                blocks.push(`<li>${inline(listMatch[2])}</li>`);
                return;
            }
            if (listType) {
                blocks.push(`</${listType}>`);
                listType = '';
            }
            if (!line) return;
            const heading = line.match(/^(#{1,3})\s+(.+)$/);
            blocks.push(heading ? `<h${heading[1].length + 2}>${inline(heading[2])}</h${heading[1].length + 2}>` : `<p>${inline(line)}</p>`);
        });
        if (listType) blocks.push(`</${listType}>`);
        return blocks.join('');
    }

    function renderMarkdown(id, value) {
        if (el(id)) el(id).innerHTML = markdown(value || '');
    }

    function startStoryScroll() {
        clearInterval(storyScrollTimer);
        const body = el('storyBody');
        body.scrollTop = 0;
        const overflow = body.scrollHeight - body.clientHeight;
        if (overflow <= 0) return 20000;
        storyScrollTimer = setInterval(() => {
            if (body.scrollTop < overflow) body.scrollTop += 1;
        }, 60);
        return Math.max(20000, 3000 + overflow * 60 + 3000);
    }

    async function showExtendedStory() {
        const item = (data.news || [])[newsIndex % Math.max((data.news || []).length, 1)];
        if (!item?.id || sceneIsActive()) return;
        try {
            const response = await fetch(`/dashboard/tv/news/${item.id}/extended`, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
            if (!response.ok) return;
            const story = (await response.json()).story;
            text('storyTitle', story.title); text('storySource', [story.customer_names, story.source_name].filter(Boolean).join(' · '));
            renderMarkdown('storySummary', story.summary); renderMarkdown('storyRelevance', story.commercial_angle); renderMarkdown('storyActions', story.suggested_action);
            el('newsIntroScreen').classList.add('active');
            setTimeout(() => {
                el('sceneWipe').classList.add('active');
                setTimeout(() => { el('newsIntroScreen').classList.remove('active'); el('storyScreen').classList.add('active'); }, 450);
                setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
                setTimeout(() => { extendedTimer = setTimeout(hideExtendedStory, startStoryScroll()); }, 1200);
            }, 3000);
        } catch (_) { /* Keep the live dashboard running if research is unavailable. */ }
    }

    function hideExtendedStory() {
        clearTimeout(extendedTimer);
        clearInterval(storyScrollTimer);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('storyScreen').classList.remove('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
    }

    function formatActivityDate(value) {
        return value ? new Date(value).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
    }

    function showPortalActivity() {
        if (sceneIsActive()) return;
        const activity = data.portal_activity || {};
        const searches = activity.searches || [];
        const quotes = activity.quote_requests || [];
        if (!searches.length && !quotes.length) return;
        const labels = {quote_analysis: 'Quote search', manual_quote_search: 'Quote search', common_parts: 'Common parts', pricing_agreements: 'Pricing agreement', suggested_parts: 'Suggested parts'};
        el('portalSearches').innerHTML = searches.slice(0, 6).map(row => `<div class="portal-row"><div><strong>${escapeHtml(row.customer_name)}</strong><span>${escapeHtml(row.user_name || labels[row.search_type] || row.search_type)}</span></div><div><b>${Number(row.parts_count || 0)} parts</b><span>${formatActivityDate(row.date_searched)}</span></div></div>`).join('') || '<div class="portal-empty">No recent searches</div>';
        el('portalQuotes').innerHTML = quotes.slice(0, 6).map(row => `<div class="portal-row"><div><strong>${escapeHtml(row.customer_name)}</strong><span>${escapeHtml(row.reference_number || 'Quote request')}</span></div><div><b>${Number(row.line_count || 0)} lines · ${escapeHtml(row.status || 'New')}</b><span>${formatActivityDate(row.date_submitted)}</span></div></div>`).join('') || '<div class="portal-empty">No quote requests</div>';
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('portalScreen').classList.add('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
        portalTimer = setTimeout(hidePortalActivity, 16000);
    }

    function sceneIsActive() {
        return ['storyScreen', 'newsIntroScreen', 'portalScreen', 'headlineScreen']
            .some(id => el(id).classList.contains('active'));
    }

    function showHeadline() {
        const items = data.news || [];
        if (!items.length || sceneIsActive()) return;
        const item = items[headlineIndex % items.length];
        const customers = (item.customers || []).map(customer => customer.name).filter(Boolean);
        text('headlineTitle', item.title || 'Customer intelligence update');
        text('headlineCustomers', customers.join(' · ') || item.customer_names || 'Customer match pending');
        text('headlineSource', item.source_name || 'News feed');
        text('headlinePublished', item.published_at ? new Date(item.published_at).toLocaleDateString([], {day: '2-digit', month: 'long', year: 'numeric'}) : 'Recently collected');
        text('headlinePosition', `${(headlineIndex % items.length) + 1} of ${items.length}`);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('headlineScreen').classList.add('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
        headlineTimer = setTimeout(hideHeadline, 14000);
    }

    function hideHeadline() {
        clearTimeout(headlineTimer);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('headlineScreen').classList.remove('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
        headlineIndex += 1;
    }

    function hidePortalActivity() {
        clearTimeout(portalTimer);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('portalScreen').classList.remove('active'), 450);
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
    setTimeout(showHeadline, 15000);
    setInterval(showHeadline, 45000);
    setInterval(showExtendedStory, 90000);
    setTimeout(showPortalActivity, 30000);
    setInterval(showPortalActivity, 120000);

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

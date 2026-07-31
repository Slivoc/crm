(() => {
    const money = new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP', maximumFractionDigits: 0});
    let data = window.TV_DASHBOARD_DATA;
    let newsIndex = 0;
    let headlineIndex = 0;
    let extendedStoryIndex = 0;
    let extendedBatchActive = false;
    let storyScrollTimer;
    // Retained by the dormant legacy scene helpers below; those scenes are no
    // longer scheduled now that the presentation has one deterministic loop.
    let headlineTimer;
    let todayHeadlinesTimer;
    const MAIN_DASHBOARD_DURATION = 45000;
    const EXTENDED_STORIES_PER_CYCLE = 5;
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
        // Models occasionally double-escape JSON newlines or Markdown punctuation.
        // Normalise those artifacts before escaping HTML and applying our small,
        // deliberately safe Markdown subset.
        const normalised = String(value || '')
            .replace(/\\r\\n|\\n|\\r/g, '\n')
            .replace(/\\([\\`*_{}\[\]()#+.!|>~•-])/g, '$1');
        const escaped = escapeHtml(normalised).replace(/\r\n?/g, '\n');
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

    const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

    async function showExtendedStory(item, {firstStory = false, lastStory = false} = {}) {
        if (!item?.id) return false;
        let story = {
            ...item,
            summary: item.summary_raw || item.body_excerpt || 'No additional article summary is available yet.',
            commercial_angle: item.customer_names
                ? `Linked customer intelligence: ${item.customer_names}`
                : 'Relevant aviation intelligence for the commercial team.',
            suggested_action: 'Review the source article and discuss any customer opportunities.'
        };
        try {
            const response = await fetch(`/dashboard/tv/news/${item.id}/extended`, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
            if (response.ok) story = (await response.json()).story;
        } catch (_) {
            // Use the snapshot detail below when live research is unavailable.
        }
        try {
            // Keep the previous story visible until the wipe covers it.
            if (!firstStory) {
                el('sceneWipe').classList.add('active');
                await wait(450);
            }
            text('storyTitle', story.title); text('storySource', [story.customer_names, story.source_name].filter(Boolean).join(' · '));
            renderMarkdown('storySummary', story.summary); renderMarkdown('storyRelevance', story.commercial_angle); renderMarkdown('storyActions', story.suggested_action);
            if (firstStory) {
                el('newsIntroScreen').classList.add('active');
                await wait(3000);
                el('sceneWipe').classList.add('active');
                await wait(450);
                el('newsIntroScreen').classList.remove('active');
            }
            el('storyScreen').classList.add('active');
            await wait(600);
            el('sceneWipe').classList.remove('active');
            await wait(startStoryScroll());
            clearInterval(storyScrollTimer);
            if (lastStory) {
                el('sceneWipe').classList.add('active');
                await wait(450);
                el('storyScreen').classList.remove('active');
                await wait(600);
                el('sceneWipe').classList.remove('active');
            }
            return true;
        } catch (_) {
            // Keep the live dashboard running if a scene transition is interrupted.
            el('newsIntroScreen').classList.remove('active');
            el('storyScreen').classList.remove('active');
            el('sceneWipe').classList.remove('active');
            clearInterval(storyScrollTimer);
            return false;
        }
    }

    async function showExtendedStoryBatch() {
        // Freeze this batch so the background refresh cannot reorder it midway.
        const items = [...(data.news || [])];
        if (!items.length || sceneIsActive()) return;

        extendedBatchActive = true;
        try {
            // Use a dedicated cursor so every queued article gets a turn. The
            // ribbon's faster rotation no longer determines extended stories.
            // Keep the cycle at exactly five, wrapping when fewer are available.
            for (let offset = 0; offset < EXTENDED_STORIES_PER_CYCLE; offset += 1) {
                const item = items[extendedStoryIndex % items.length];
                extendedStoryIndex = (extendedStoryIndex + 1) % items.length;
                await showExtendedStory(item, {
                    firstStory: offset === 0,
                    lastStory: offset === EXTENDED_STORIES_PER_CYCLE - 1
                });
            }
        } finally {
            extendedBatchActive = false;
        }
    }

    function formatActivityDate(value) {
        return value ? new Date(value).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';
    }

    async function showPortalActivity() {
        if (sceneIsActive()) return false;
        const activity = data.portal_activity || {};
        const searches = activity.searches || [];
        const quotes = activity.quote_requests || [];
        if (!searches.length && !quotes.length) return false;
        const labels = {quote_analysis: 'Quote search', manual_quote_search: 'Quote search', common_parts: 'Common parts', pricing_agreements: 'Pricing agreement', suggested_parts: 'Suggested parts'};
        el('portalSearches').innerHTML = searches.slice(0, 6).map(row => `<div class="portal-row"><div><strong>${escapeHtml(row.customer_name)}</strong><span>${escapeHtml(row.user_name || labels[row.search_type] || row.search_type)}</span></div><div><b>${Number(row.parts_count || 0)} parts</b><span>${formatActivityDate(row.date_searched)}</span></div></div>`).join('') || '<div class="portal-empty">No recent searches</div>';
        el('portalQuotes').innerHTML = quotes.slice(0, 6).map(row => `<div class="portal-row"><div><strong>${escapeHtml(row.customer_name)}</strong><span>${escapeHtml(row.reference_number || 'Quote request')}</span></div><div><b>${Number(row.line_count || 0)} lines · ${escapeHtml(row.status || 'New')}</b><span>${formatActivityDate(row.date_submitted)}</span></div></div>`).join('') || '<div class="portal-empty">No quote requests</div>';
        el('sceneWipe').classList.add('active');
        await wait(450);
        el('portalScreen').classList.add('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        await wait(PORTAL_ACTIVITY_DURATION);
        await hidePortalActivity();
        return true;
    }

    function sceneIsActive() {
        if (extendedBatchActive) return true;
        return ['storyScreen', 'newsIntroScreen', 'portalScreen', 'headlineScreen', 'todayHeadlinesScreen']
            .some(id => el(id).classList.contains('active'));
    }

    function showTodayHeadlines() {
        if (sceneIsActive()) return;
        const items = (data.news || []).filter(item => item.is_today).slice(0, 8);
        if (!items.length) return;
        el('todayHeadlinesList').innerHTML = items.map((item, index) => `
            <li>
                <span>${String(index + 1).padStart(2, '0')}</span>
                <div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml([item.customer_names, item.source_name].filter(Boolean).join(' · '))}</small></div>
            </li>`).join('');
        text('todayHeadlinesDate', new Date().toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}));
        text('todayHeadlinesCount', `${items.length} headline${items.length === 1 ? '' : 's'} today`);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('todayHeadlinesScreen').classList.add('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
        todayHeadlinesTimer = setTimeout(hideTodayHeadlines, 18000);
    }

    function hideTodayHeadlines() {
        clearTimeout(todayHeadlinesTimer);
        el('sceneWipe').classList.add('active');
        setTimeout(() => el('todayHeadlinesScreen').classList.remove('active'), 450);
        setTimeout(() => el('sceneWipe').classList.remove('active'), 1050);
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

    async function hidePortalActivity() {
        el('sceneWipe').classList.add('active');
        await wait(450);
        el('portalScreen').classList.remove('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
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

    async function runPresentation() {
        // One loop owns every full-screen scene, so timer collisions cannot
        // randomly skip content. Each return to metrics follows a full batch.
        while (true) {
            await wait(MAIN_DASHBOARD_DURATION);
            await showExtendedStoryBatch();
        }
    }

    fitDashboard();
    render(data);
    window.addEventListener('resize', fitDashboard);
    setInterval(() => { newsIndex += 1; renderNews(); }, 9000);
    setInterval(refresh, 15000);
    runPresentation();

})();

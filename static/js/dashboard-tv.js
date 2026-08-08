(() => {
    const money = new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP', maximumFractionDigits: 0});
    let data = window.TV_DASHBOARD_DATA;
    let newsIndex = 0;
    let headlineIndex = 0;
    let extendedStoryIndex = 0;
    let shownStoryIds = new Set();
    let extendedBatchActive = false;
    let lastFactId = null;
    let lastFocusCustomerId = null;
    let manualFactMode = false;
    let manualFactIndex = -1;
    let storyScrollTimer;
    // Retained by the dormant legacy scene helpers below; those scenes are no
    // longer scheduled now that the presentation has one deterministic loop.
    let headlineTimer;
    let todayHeadlinesTimer;
    const MAIN_DASHBOARD_DURATION = 45000;
    const PORTAL_ACTIVITY_DURATION = 16000;
    const CUSTOMER_FOCUS_DURATION = 16000;
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
        const pacePercentage = Math.min(Math.max(Number(snapshot.sales.pace_percentage || 0), 0), 100);
        const paceMarker = el('salesPaceMarker');
        paceMarker.style.left = `${pacePercentage}%`;
        paceMarker.style.display = snapshot.sales.target ? '' : 'none';
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

    function stripHtml(value) {
        if (!value) return '';
        const parsed = new DOMParser().parseFromString(String(value), 'text/html');
        return (parsed.body.textContent || '').replace(/\s+/g, ' ').trim();
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
        if (!item?.id || manualFactMode) return false;
        let story = {
            ...item,
            summary: item.body_excerpt || stripHtml(item.summary_raw) || 'No additional article summary is available yet.',
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
        if (manualFactMode) return false;
        try {
            // Keep the previous story visible until the wipe covers it.
            if (!firstStory) {
                el('sceneWipe').classList.add('active');
                await wait(450);
                if (manualFactMode) return false;
            }
            text('storyTitle', story.title); text('storySource', [story.customer_names, story.source_name].filter(Boolean).join(' · '));
            el('storyFreshness').classList.toggle('active', Boolean(item.is_today));
            renderMarkdown('storySummary', /<\/?[a-z][\s\S]*>/i.test(story.summary || '') ? stripHtml(story.summary) : story.summary);
            renderMarkdown('storyRelevance', story.commercial_angle); renderMarkdown('storyActions', story.suggested_action);
            if (firstStory) {
                el('newsIntroScreen').classList.add('active');
                await wait(3000);
                if (manualFactMode) return false;
                el('sceneWipe').classList.add('active');
                await wait(450);
                if (manualFactMode) return false;
                el('newsIntroScreen').classList.remove('active');
            }
            el('storyScreen').classList.add('active');
            await wait(600);
            el('sceneWipe').classList.remove('active');
            await wait(startStoryScroll());
            clearInterval(storyScrollTimer);
            if (manualFactMode) return false;
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

        await showTodayHeadlines();
        const batch = buildStoryBatch(items);
        extendedBatchActive = true;
        try {
            for (let offset = 0; offset < batch.length; offset += 1) {
                if (manualFactMode) break;
                const item = batch[offset];
                await showExtendedStory(item, {
                    firstStory: offset === 0,
                    lastStory: offset === batch.length - 1
                });
            }
        } finally {
            extendedBatchActive = false;
        }
    }

    function buildStoryBatch(items) {
        const now = Date.now();
        const ageDays = item => Math.max(0, (now - new Date(item.published_at).getTime()) / 86400000);
        const ranked = [...items].sort((a, b) =>
            Number(b.editorial_score ?? b.relevance_score ?? 0) - Number(a.editorial_score ?? a.relevance_score ?? 0) ||
            new Date(b.published_at) - new Date(a.published_at));
        if (ranked.filter(item => !shownStoryIds.has(item.id)).length < Math.min(EXTENDED_STORIES_PER_CYCLE, ranked.length)) {
            shownStoryIds = new Set();
        }
        const batch = [];
        const take = (predicate, count) => {
            ranked.filter(item => !shownStoryIds.has(item.id) && !batch.some(chosen => chosen.id === item.id) && predicate(item))
                .slice(0, count).forEach(item => batch.push(item));
        };
        take(item => ageDays(item) <= 2, 2);
        take(item => ageDays(item) > 2 && ageDays(item) <= 14, 2);
        take(item => ageDays(item) > 14 && ageDays(item) <= 45, 1);
        take(() => true, EXTENDED_STORIES_PER_CYCLE - batch.length);
        batch.forEach(item => shownStoryIds.add(item.id));
        return batch;
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
        if (manualFactMode) return false;
        el('portalScreen').classList.add('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        await wait(PORTAL_ACTIVITY_DURATION);
        if (manualFactMode) return true;
        await hidePortalActivity();
        return true;
    }

    function sceneIsActive() {
        if (extendedBatchActive) return true;
        return ['storyScreen', 'newsIntroScreen', 'portalScreen', 'headlineScreen', 'todayHeadlinesScreen', 'customerFocusScreen', 'factScreen']
            .some(id => el(id).classList.contains('active'));
    }

    async function loadCustomerFocusInsight(customerId) {
        text('focusCustomerDescription', 'Loading current company research…');
        el('focusSimilarCompanies').innerHTML = '<li>Researching comparable organisations…</li>';
        try {
            const response = await fetch(`/dashboard/tv/customers/${customerId}/focus`, {
                headers: {'Accept': 'application/json'}, cache: 'no-store'
            });
            const body = await response.json();
            if (!response.ok) throw new Error(body.error || 'Research unavailable');
            if (lastFocusCustomerId !== customerId) return;
            const insight = body.insight || {};
            text('focusCustomerDescription', insight.description || 'Recent MGC customer with active purchasing activity.');
            const similar = insight.similar_companies || [];
            el('focusSimilarCompanies').innerHTML = similar.length
                ? similar.map(company => `<li title="${escapeAttribute(company.reason || '')}"><strong>${escapeHtml(company.name)}</strong>${company.reason ? ` · ${escapeHtml(company.reason)}` : ''}</li>`).join('')
                : '<li>No verified comparable organisations returned.</li>';
        } catch (_) {
            if (lastFocusCustomerId !== customerId) return;
            text('focusCustomerDescription', 'Recent MGC customer with active purchasing activity in the last 90 days.');
            el('focusSimilarCompanies').innerHTML = '<li>Company research temporarily unavailable.</li>';
        }
    }

    async function showCustomerFocus() {
        const customers = data.customer_focus || [];
        if (!customers.length || sceneIsActive()) return false;
        let choices = customers.filter(item => item.id !== lastFocusCustomerId);
        if (!choices.length) choices = customers;
        const item = choices[Math.floor(Math.random() * choices.length)];
        lastFocusCustomerId = item.id;

        text('focusCustomerName', item.name || 'Customer');
        text('focusCustomerCountry', item.country || 'International');
        text('focusCustomerStatus', item.customer_status || 'Customer');
        text('focusCustomerOwner', item.salesperson_name ? `Account owner · ${item.salesperson_name}` : 'MGC customer relationship');
        text('focusCustomerSpend', money.format(Number(item.spend_90d || 0)));
        text('focusCustomerOrders', Number(item.order_count_90d || 0).toLocaleString());
        text('focusCustomerLastOrder', item.last_order_ref || 'Recent order');
        text('focusCustomerLastDate', item.last_order_date ? new Date(item.last_order_date).toLocaleDateString([], {day:'2-digit', month:'long', year:'numeric'}) : 'Within the last 90 days');
        text('focusCustomerPosition', `${customers.length} eligible customer${customers.length === 1 ? '' : 's'}`);

        const logo = el('focusCustomerLogo');
        const initial = el('focusCustomerInitial');
        initial.textContent = (item.name || '?').charAt(0).toUpperCase();
        logo.style.display = item.logo_url ? 'block' : 'none';
        initial.style.display = item.logo_url ? 'none' : '';
        logo.onerror = () => { logo.style.display = 'none'; initial.style.display = ''; };
        logo.src = item.logo_url || '';
        const parts = item.most_ordered_parts || [];
        el('focusCustomerParts').innerHTML = parts.length
            ? parts.map(part => `<span class="customer-focus-part"><strong>${escapeHtml(part.base_part_number)}</strong> · ${Number(part.order_count || 0)} order${Number(part.order_count || 0) === 1 ? '' : 's'}</span>`).join('')
            : '<span class="customer-focus-part">No purchased parts recorded</span>';
        loadCustomerFocusInsight(item.id);

        el('sceneWipe').classList.add('active');
        await wait(450);
        if (manualFactMode) return false;
        el('customerFocusScreen').classList.add('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        await wait(CUSTOMER_FOCUS_DURATION);
        if (manualFactMode) return true;
        el('sceneWipe').classList.add('active');
        await wait(450);
        el('customerFocusScreen').classList.remove('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        return true;
    }

    function renderAerospaceFact(item) {
        lastFactId = item.id;
        text('factTopic', item.topic); text('factTitle', item.title); text('factSubtitle', item.subtitle);
        el('factList').innerHTML = (item.facts || []).map(fact => `<li>${escapeHtml(fact)}</li>`).join('');
        text('factCredit', item.image_credit ? `Image: ${item.image_credit}` : '');
        el('factImage').style.backgroundImage = item.image_url ? `url("${escapeAttribute(item.image_url)}")` : '';
        el('factScreen').classList.toggle('has-image', Boolean(item.image_url));
    }

    async function showAerospaceFact() {
        const facts = data.aerospace_facts || [];
        if (!facts.length || sceneIsActive()) return false;
        let choices = facts.filter(item => item.id !== lastFactId);
        if (!choices.length) choices = facts;
        const item = choices[Math.floor(Math.random() * choices.length)];
        renderAerospaceFact(item);
        el('sceneWipe').classList.add('active'); await wait(450); el('factScreen').classList.add('active');
        await wait(600); el('sceneWipe').classList.remove('active'); await wait(18000);
        if (manualFactMode) return true;
        el('sceneWipe').classList.add('active'); await wait(450); el('factScreen').classList.remove('active');
        await wait(600); el('sceneWipe').classList.remove('active'); return true;
    }

    async function showTodayHeadlines() {
        if (sceneIsActive()) return false;
        const items = (data.news || []).filter(item => item.is_today).slice(0, 8);
        if (!items.length) return false;
        el('todayHeadlinesList').innerHTML = items.map((item, index) => `
            <li>
                <span>${String(index + 1).padStart(2, '0')}</span>
                <div><strong><span class="news-new-badge">NEW</span>${escapeHtml(item.title)}</strong><small>${escapeHtml([item.customer_names, item.source_name].filter(Boolean).join(' · '))}</small></div>
            </li>`).join('');
        text('todayHeadlinesDate', new Date().toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}));
        text('todayHeadlinesCount', `${items.length} headline${items.length === 1 ? '' : 's'} today`);
        el('sceneWipe').classList.add('active');
        await wait(450);
        if (manualFactMode) return false;
        el('todayHeadlinesScreen').classList.add('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        await wait(12000);
        if (manualFactMode) return true;
        el('sceneWipe').classList.add('active');
        await wait(450);
        el('todayHeadlinesScreen').classList.remove('active');
        await wait(600);
        el('sceneWipe').classList.remove('active');
        return true;
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

    function showManualFact(direction) {
        const facts = data.aerospace_facts || [];
        if (!facts.length) return;
        if (!manualFactMode) {
            manualFactMode = true;
            const currentIndex = facts.findIndex(item => item.id === lastFactId);
            manualFactIndex = currentIndex >= 0
                ? (currentIndex + direction + facts.length) % facts.length
                : (direction > 0 ? 0 : facts.length - 1);
            clearInterval(storyScrollTimer);
            clearTimeout(headlineTimer);
            clearTimeout(todayHeadlinesTimer);
            ['storyScreen', 'newsIntroScreen', 'portalScreen', 'headlineScreen', 'todayHeadlinesScreen', 'customerFocusScreen']
                .forEach(id => el(id).classList.remove('active'));
            el('sceneWipe').classList.remove('active');
        } else {
            manualFactIndex = (manualFactIndex + direction + facts.length) % facts.length;
        }
        renderAerospaceFact(facts[manualFactIndex]);
        el('factScreen').classList.add('active');
        const hint = el('factDebugHint');
        hint.hidden = false;
        hint.textContent = `Manual preview  ${manualFactIndex + 1} / ${facts.length}  ·  ← → browse  ·  Esc resume`;
    }

    function leaveManualFactMode() {
        if (!manualFactMode) return;
        // Restart cleanly so no timer that was paused mid-transition can flash
        // an old scene after manual preview closes.
        window.location.reload();
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
            await showPortalActivity();
            await showCustomerFocus();
            await showAerospaceFact();
            await showExtendedStoryBatch();
        }
    }

    fitDashboard();
    render(data);
    window.addEventListener('resize', fitDashboard);
    window.addEventListener('keydown', event => {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            event.preventDefault();
            showManualFact(event.key === 'ArrowRight' ? 1 : -1);
        } else if (event.key === 'Escape') {
            leaveManualFactMode();
        }
    });
    setInterval(() => { newsIndex += 1; renderNews(); }, 9000);
    setInterval(refresh, 15000);
    runPresentation();

})();

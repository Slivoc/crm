/**
 * Salesperson Metrics Tab JavaScript
 */

class SalespersonMetrics {
  constructor(customerId, salesPersonId) {
    this.customerId = customerId;
    this.salesPersonId = salesPersonId;
    this.currentPeriod = 'today'; // Default period
    this.charts = {};
    this.activityFilters = 'all';

    // Debug logging
    console.log('SalespersonMetrics initialized with:', {
      customerId: this.customerId,
      salesPersonId: this.salesPersonId
    });

    this.init();
  }

  init() {
    console.log('Initializing salesperson metrics');
    this.bindEvents();
    this.loadMetrics();
  }

  bindEvents() {
    // Period selection buttons
    document.querySelectorAll('.period-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        this.currentPeriod = e.target.dataset.period;
        this.loadMetrics();
      });
    });

    // Refresh button
    const refreshBtn = document.getElementById('refreshMetricsBtn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => {
        this.loadMetrics();
      });
    } else {
      console.warn('Refresh button not found');
    }

    // Activity filters
    document.querySelectorAll('.activity-filter').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.activity-filter').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        this.activityFilters = e.target.dataset.filter;
        this.filterActivityTimeline();
      });
    });

    // Tab shown event - redraw charts when tab becomes visible
    const metricsTab = document.querySelector('a[href="#salesperson-metrics"]');
    if (metricsTab) {
      metricsTab.addEventListener('shown.bs.tab', () => {
        console.log('Metrics tab shown, resizing charts');
        this.resizeCharts();
      });
    } else {
      console.warn('Metrics tab link not found');
    }
  }

  loadMetrics() {
    // Show loading states
    this.showLoadingState();

    // API parameters
    const params = new URLSearchParams({
      period: this.currentPeriod,
      salesperson_id: this.salesPersonId
    });

    // Use the correct URL - match your actual API endpoint
    const apiUrl = `/metrics/customers/${this.customerId}?${params.toString()}`;
    console.log('Fetching metrics from:', apiUrl);

    fetch(apiUrl)
      .then(response => {
        console.log('API response status:', response.status);
        if (!response.ok) throw new Error(`Failed to load metrics: ${response.status} ${response.statusText}`);
        return response.json();
      })
      .then(data => {
        console.log('API response data:', data);
        if (data.success) {
          this.updateDashboard(data.data);
        } else {
          throw new Error(data.error || 'Unknown error');
        }
      })
      .catch(error => {
        console.error('Error loading metrics:', error);
        this.showError(error.message);
      });
  }

  showLoadingState() {
    // Update stats with loading indicators
    ['orders', 'quotes', 'comms', 'customers'].forEach(stat => {
      document.getElementById(`stats-${stat}`)?.classList.add('placeholder', 'w-50');
      document.getElementById(`stats-${stat}-prev`)?.classList.add('placeholder', 'w-75');

      if (document.getElementById(`stats-${stat}-value`)) {
        document.getElementById(`stats-${stat}-value`).classList.add('placeholder', 'w-50');
      }
    });

    // Timeline loading
    document.getElementById('activity-timeline').innerHTML = `
      <div class="text-center py-4 text-muted">
        <div class="spinner-border spinner-border-sm" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        <p class="mb-0 mt-2">Loading activity...</p>
      </div>
    `;
  }

  removeLoadingState() {
    // Remove loading placeholders
    document.querySelectorAll('.placeholder').forEach(el => {
      el.classList.remove('placeholder', 'w-50', 'w-75');
    });
  }

  showError(message) {
    // Remove loading state
    this.removeLoadingState();

    // Show error in timeline
    document.getElementById('activity-timeline').innerHTML = `
      <div class="alert alert-danger m-3">
        <i class="bi bi-exclamation-triangle-fill me-2"></i>
        ${message}
      </div>
    `;

    // Reset stats
    ['orders', 'quotes', 'comms', 'customers'].forEach(stat => {
      document.getElementById(`stats-${stat}`).textContent = '-';
      document.getElementById(`stats-${stat}-prev`).textContent = 'Data unavailable';

      if (document.getElementById(`stats-${stat}-value`)) {
        document.getElementById(`stats-${stat}-value`).textContent = '-';
      }
    });
  }

 // Update the updateDashboard method in the SalespersonMetrics class
// This replaces the existing "Customers" section with our new "Contacts" focus

updateDashboard(data) {
  this.removeLoadingState();

  // Update summary metrics
  if (data.summary) {
    const summary = data.summary;

    // Orders
    document.getElementById('stats-orders').textContent = summary.orders.count || 0;
    document.getElementById('stats-orders-value').textContent = this.formatCurrency(summary.orders.value);
    document.getElementById('stats-orders-prev').textContent = this.formatChange(summary.orders.change);
    document.getElementById('stats-orders-prev').className =
      `text-muted small mt-1 ${summary.orders.change >= 0 ? 'text-success' : 'text-danger'}`;

    // Quotes
    document.getElementById('stats-quotes').textContent = summary.quotes.count || 0;
    document.getElementById('stats-quotes-value').textContent = this.formatCurrency(summary.quotes.value);
    document.getElementById('stats-quotes-prev').textContent = this.formatChange(summary.quotes.change);
    document.getElementById('stats-quotes-prev').className =
      `text-muted small mt-1 ${summary.quotes.change >= 0 ? 'text-success' : 'text-danger'}`;

    // Communications
    document.getElementById('stats-comms').textContent = summary.communications.count || 0;
    document.getElementById('stats-comms-prev').textContent = this.formatChange(summary.communications.change);
    document.getElementById('stats-comms-prev').className =
      `text-muted small mt-1 ${summary.communications.change >= 0 ? 'text-success' : 'text-danger'}`;
    document.getElementById('stats-comms-emails').textContent = `${summary.communications.emails || 0} emails`;
    document.getElementById('stats-comms-calls').textContent = `${summary.communications.calls || 0} calls`;

    // Contacts (replaces Customers)
    document.getElementById('stats-contacts').textContent = summary.customers.contacts || 0;
    document.getElementById('stats-contacts-prev').textContent = this.formatChange(summary.customers.contactsChange);
    document.getElementById('stats-contacts-prev').className =
      `text-muted small mt-1 ${summary.customers.contactsChange >= 0 ? 'text-success' : 'text-danger'}`;
    document.getElementById('stats-communication-types').textContent =
      `${summary.customers.totalCommunications || 0} communications`;
  }

  // Update timeline
  if (data.activities) {
    this.renderActivityTimeline(data.activities);
  }

  // Update charts
  this.updateCharts(data);
}

  renderActivityTimeline(activities) {
    if (!activities || activities.length === 0) {
      document.getElementById('activity-timeline').innerHTML = `
        <div class="text-center py-4 text-muted">
          <i class="bi bi-calendar-x fs-3"></i>
          <p class="mb-0 mt-2">No activity found for this period</p>
        </div>
      `;
      return;
    }

    // Sort activities by date, newest first
    activities.sort((a, b) => new Date(b.date) - new Date(a.date));

    let timelineHtml = '';
    let currentDate = null;

    activities.forEach(activity => {
      // Add data-type attribute for filtering
      const activityType = activity.type === 'email' || activity.type === 'call' ? 'comm' : activity.type;

      // Format the activity date
      const activityDate = new Date(activity.date);
      const dateStr = this.formatDate(activityDate);

      // Add date separator if date changes
      if (currentDate !== dateStr) {
        currentDate = dateStr;
        timelineHtml += `
          <div class="date-separator">
            <div class="date-line"></div>
            <div class="date-label">${this.isToday(activityDate) ? 'Today' : dateStr}</div>
          </div>
        `;
      }

      // Format time
      const timeStr = activityDate.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit'
      });

      // Determine icon and color based on activity type
      let icon, color, badge, title;
      switch (activity.type) {
        case 'order':
          icon = 'bi-bag-check-fill';
          color = 'text-primary';
          badge = 'bg-primary';
          title = 'New Order';
          break;
        case 'quote':
          icon = 'bi-file-earmark-text-fill';
          color = 'text-success';
          badge = 'bg-success';
          title = 'New Quote';
          break;
        case 'email':
          icon = 'bi-envelope-fill';
          color = 'text-info';
          badge = 'bg-info';
          title = 'Email';
          break;
        case 'call':
          icon = 'bi-telephone-fill';
          color = 'text-secondary';
          badge = 'bg-secondary';
          title = 'Phone Call';
          break;
        default:
          icon = 'bi-star-fill';
          color = 'text-warning';
          badge = 'bg-warning';
          title = 'Activity';
      }

      // Build the activity item HTML
      timelineHtml += `
        <div class="activity-item" data-type="${activityType}">
          <div class="activity-icon">
            <i class="bi ${icon} ${color}"></i>
          </div>
          <div class="activity-content">
            <div class="activity-header">
              <span class="activity-time">${timeStr}</span>
              <span class="badge ${badge}">${title}</span>
            </div>
            <div class="activity-title">${activity.title || 'Untitled'}</div>
            <div class="activity-details">
              ${activity.description || ''}
              ${activity.value ? `<div class="activity-value">${this.formatCurrency(activity.value)}</div>` : ''}
            </div>
            ${activity.customer ? `
              <div class="activity-customer">
                <i class="bi bi-building"></i> ${activity.customer}
              </div>
            ` : ''}
            ${activity.contact ? `
              <div class="activity-contact">
                <i class="bi bi-person"></i> ${activity.contact}
              </div>
            ` : ''}
          </div>
        </div>
      `;
    });

    document.getElementById('activity-timeline').innerHTML = timelineHtml;
    this.filterActivityTimeline();
  }

  filterActivityTimeline() {
    const items = document.querySelectorAll('.activity-item');
    if (this.activityFilters === 'all') {
      items.forEach(item => item.style.display = '');
    } else {
      items.forEach(item => {
        if (item.dataset.type === this.activityFilters) {
          item.style.display = '';
        } else {
          item.style.display = 'none';
        }
      });
    }
  }

  updateCharts(data) {
    this.updateConversionFunnel(data.funnel || {
      leads: 0,
      quotes: 0,
      orders: 0
    });

    this.updateCommunicationChart(data.communications || {
      email: 0,
      call: 0
    });
  }

  updateConversionFunnel(data) {
    const ctx = document.getElementById('conversionFunnelChart');

    if (this.charts.funnel) {
      this.charts.funnel.destroy();
    }

    this.charts.funnel = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['Leads', 'Quotes', 'Orders'],
        datasets: [{
          label: 'Conversion Funnel',
          data: [data.leads || 0, data.quotes || 0, data.orders || 0],
          backgroundColor: [
            'rgba(255, 193, 7, 0.7)',
            'rgba(40, 167, 69, 0.7)',
            'rgba(13, 110, 253, 0.7)'
          ],
          borderColor: [
            'rgb(255, 193, 7)',
            'rgb(40, 167, 69)',
            'rgb(13, 110, 253)'
          ],
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false
          },
          tooltip: {
            callbacks: {
              label: function(context) {
                return context.raw + ' ' + context.label.toLowerCase();
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              precision: 0
            }
          }
        }
      }
    });
  }

  updateCommunicationChart(data) {
    const ctx = document.getElementById('communicationChart');

    if (this.charts.communication) {
      this.charts.communication.destroy();
    }

    this.charts.communication = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Emails', 'Phone Calls'],
        datasets: [{
          data: [data.email || 0, data.call || 0],
          backgroundColor: [
            'rgba(13, 202, 240, 0.7)',
            'rgba(108, 117, 125, 0.7)'
          ],
          borderColor: [
            'rgb(13, 202, 240)',
            'rgb(108, 117, 125)'
          ],
          borderWidth: 1
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom'
          }
        }
      }
    });
  }

  resizeCharts() {
    if (this.charts.funnel) {
      this.charts.funnel.resize();
    }

    if (this.charts.communication) {
      this.charts.communication.resize();
    }
  }

  // Helper functions
  formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD'
    }).format(value || 0);
  }

  formatChange(value) {
    const sign = value >= 0 ? '+' : '';
    return `${sign}${value}% vs previous`;
  }

  formatDate(date) {
    return date.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric'
    });
  }

  isToday(date) {
    const today = new Date();
    return date.getDate() === today.getDate() &&
      date.getMonth() === today.getMonth() &&
      date.getFullYear() === today.getFullYear();
  }
}

document.addEventListener('DOMContentLoaded', function() {
  console.log('DOM loaded, setting up salesperson metrics');

  // Get required IDs from the page
  const customerId = document.querySelector('[data-customer-id]')?.dataset?.customerId;
  const salesPersonId = document.getElementById('salesperson_id')?.value;

  console.log('Found IDs:', { customerId, salesPersonId });

  if (customerId && salesPersonId) {
    // Initialize the metrics module
    console.log('Creating SalespersonMetrics instance');
    window.salespersonMetrics = new SalespersonMetrics(customerId, salesPersonId);

    // Monitor for salesperson changes
    document.getElementById('salesperson_id')?.addEventListener('change', function() {
      console.log('Salesperson changed to:', this.value);
      window.salespersonMetrics.salesPersonId = this.value;
      window.salespersonMetrics.loadMetrics();
    });
  } else {
    console.error('Missing required data: customerId or salesPersonId not found');
  }
});

function debugTabStructure() {
  console.log('Debugging tab structure:');

  // Check tab link
  const tabLink = document.querySelector('a[href="#salesperson-metrics"]');
  console.log('Tab link:', tabLink);

  // Check content container
  const tabContent = document.getElementById('salesperson-metrics');
  console.log('Tab content container:', tabContent);
  console.log('Tab content visibility:', tabContent ?
    window.getComputedStyle(tabContent).display : 'N/A');

  // Check tab is included in parent's tab-content
  if (tabContent && tabContent.parentElement) {
    console.log('Tab content is child of:', tabContent.parentElement);
    console.log('Other tab panes in parent:',
      Array.from(tabContent.parentElement.querySelectorAll('.tab-pane')).map(el => el.id));
  }
}

// Add this to your existing code
const metricsTab = document.querySelector('a[href="#salesperson-metrics"]');
if (metricsTab) {
  metricsTab.addEventListener('shown.bs.tab', function() {
    console.log('Metrics tab shown event fired');
    debugTabStructure();

    // Call resize after a brief delay to make sure content is visible
    setTimeout(function() {
      if (window.salespersonMetrics) {
        window.salespersonMetrics.resizeCharts();
      }
    }, 100);
  });

  // Also add click handler for direct debugging
  metricsTab.addEventListener('click', function() {
    console.log('Metrics tab clicked');
  });
}

// Call on page load to see initial state
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(debugTabStructure, 500);
});

// Fix for duplicate salesperson-metrics tab content
function fixDuplicateMetricsTab() {
  console.log('Checking for duplicate salesperson-metrics tabs...');
  const metricsTabs = document.querySelectorAll('#salesperson-metrics');

  if (metricsTabs.length > 1) {
    console.log(`Found ${metricsTabs.length} elements with id="salesperson-metrics"`);

    // Check which one has content
    let hasContent = [];
    metricsTabs.forEach((tab, index) => {
      const metricsContainer = tab.querySelector('.salesperson-metrics-container');
      const hasMetricsContainer = !!metricsContainer;

      console.log(`Tab ${index}: Has metrics container: ${hasMetricsContainer}`);
      console.log(`Tab ${index} HTML:`, tab.innerHTML.slice(0, 100) + '...');

      if (hasMetricsContainer) {
        hasContent.push(index);
      }
    });

    // If both have content, keep the first one
    // If only one has content, keep that one
    const indexToKeep = hasContent.length > 0 ? hasContent[0] : 0;

    console.log(`Keeping tab at index ${indexToKeep} and removing others`);

    // Remove all but the one to keep
    metricsTabs.forEach((tab, index) => {
      if (index !== indexToKeep) {
        console.log(`Removing duplicate tab at index ${index}`);
        tab.remove();
      }
    });
  } else {
    console.log('No duplicate salesperson-metrics tabs found');
  }
}

// Call this function when the DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(fixDuplicateMetricsTab, 100);
});
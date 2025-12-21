/**
 * Salesperson Metrics Tab JavaScript
 *
 * Handles the loading and display of salesperson activity metrics
 */

class SalespersonMetrics {
  constructor(customerId, salesPersonId) {
    this.customerId = customerId;
    this.salesPersonId = salesPersonId;
    this.currentPeriod = 'today'; // Default period
    this.charts = {};
    this.activityFilters = 'all';

    this.init();
  }

  init() {
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
    document.getElementById('refreshMetricsBtn')?.addEventListener('click', () => {
      this.loadMetrics();
    });

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
        this.resizeCharts();
      });
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

    if (this.customerId) {
      params.append('customer_id', this.customerId);
    }

    // Fetch the metrics data from API
fetch(`/salesperson/api/metrics?${params.toString()}`, {
  headers: {
    'X-API-Key': 'dingleberry'
  }
})
      .then(response => {
        if (!response.ok) throw new Error('Failed to load metrics');
        return response.json();
      })
      .then(data => {
        if (data.success) {
          this.updateDashboard(data.data);
        } else {
          throw new Error(data.error || 'Unknown error');
        }
      })
      .catch(error => {
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

      // Customers
      document.getElementById('stats-customers').textContent = summary.customers.count || 0;
      document.getElementById('stats-customers-prev').textContent = this.formatChange(summary.customers.change);
      document.getElementById('stats-customers-prev').className =
        `text-muted small mt-1 ${summary.customers.change >= 0 ? 'text-success' : 'text-danger'}`;
      document.getElementById('stats-contacts').textContent = `${summary.customers.contacts || 0} contacts`;
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
  // Add Salesperson Metrics tab to nav tabs
  const tabsNav = document.querySelector('.nav.nav-tabs');
  if (tabsNav) {
    const metricsTabItem = document.createElement('li');
    metricsTabItem.className = 'nav-item';
    metricsTabItem.innerHTML = `
      <a class="nav-link" href="#salesperson-metrics" data-bs-toggle="tab">
        <i class="bi bi-graph-up me-1"></i> Metrics
      </a>
    `;
    tabsNav.appendChild(metricsTabItem);
  }

  // Get required IDs from the page
  const customerId = document.querySelector('[data-customer-id]')?.dataset?.customerId;

  // We need to get salesperson ID from the salesperson select input
  const salesPersonId = document.getElementById('salesperson_id')?.value;

  if (customerId && salesPersonId) {
    // Initialize the metrics module
    window.salespersonMetrics = new SalespersonMetrics(customerId, salesPersonId);

    // Monitor for salesperson changes
    document.getElementById('salesperson_id')?.addEventListener('change', function() {
      window.salespersonMetrics.salesPersonId = this.value;
      window.salespersonMetrics.loadMetrics();
    });
  }
});
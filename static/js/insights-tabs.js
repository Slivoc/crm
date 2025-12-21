// Add this code to convert the insights section to tabs

function convertInsightsToTabs() {
  console.log("Converting insights to tabs...");

  // Override the renderInsights function
  window.renderInsights = function(insights) {
    if (!insights) {
      return '<div class="text-center p-4">No insights available</div>';
    }

    // Create tab navigation
    let html = `
      <div class="insights-container">
        <ul class="nav nav-tabs mb-3" id="insightsTabs" role="tablist">
    `;

    // Add tab headers
    if (insights.topProducts?.data) {
      html += `
        <li class="nav-item" role="presentation">
          <button class="nav-link active" id="top-products-tab" data-bs-toggle="tab"
                  data-bs-target="#top-products-content" type="button" role="tab"
                  aria-controls="top-products-content" aria-selected="true">
            Top Products
          </button>
        </li>
      `;
    }

    if (insights.topManufacturers?.data) {
      html += `
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="top-manufacturers-tab" data-bs-toggle="tab"
                  data-bs-target="#top-manufacturers-content" type="button" role="tab"
                  aria-controls="top-manufacturers-content" aria-selected="false">
            Top Manufacturers
          </button>
        </li>
      `;
    }

    if (insights.monthlySales?.data) {
      html += `
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="monthly-sales-tab" data-bs-toggle="tab"
                  data-bs-target="#monthly-sales-content" type="button" role="tab"
                  aria-controls="monthly-sales-content" aria-selected="false">
            Monthly Sales
          </button>
        </li>
      `;
    }

    if (insights.yearlySales?.data) {
      html += `
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="yearly-sales-tab" data-bs-toggle="tab"
                  data-bs-target="#yearly-sales-content" type="button" role="tab"
                  aria-controls="yearly-sales-content" aria-selected="false">
            10-Year History
          </button>
        </li>
      `;
    }

    html += `
        </ul>
        <div class="tab-content" id="insightsTabContent">
    `;

    // Add tab content panels
    if (insights.topProducts?.data) {
      html += `
        <div class="tab-pane fade show active" id="top-products-content" role="tabpanel"
             aria-labelledby="top-products-tab">
          <div class="row">
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body" style="height: 400px;">
                  <h6 class="card-title">Top Products by Quantity</h6>
                  <canvas id="topProductsChart" style="height: 350px;"></canvas>
                </div>
              </div>
            </div>
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body">
                  <h6 class="card-title">Top Products Details</h6>
                  <div class="table-responsive">
                    <table class="table table-sm">
                      <thead>
                        <tr>
                          <th>Product</th>
                          <th class="text-end">Quantity</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${insights.topProducts.data.labels.map((label, index) => `
                          <tr>
                            <td>${label}</td>
                            <td class="text-end">${insights.topProducts.data.datasets[0].data[index].toLocaleString()}</td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    if (insights.topManufacturers?.data) {
      html += `
        <div class="tab-pane fade" id="top-manufacturers-content" role="tabpanel"
             aria-labelledby="top-manufacturers-tab">
          <div class="row">
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body" style="height: 400px;">
                  <h6 class="card-title">Top Manufacturers</h6>
                  <canvas id="topManufacturersChart" style="height: 350px;"></canvas>
                </div>
              </div>
            </div>
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body">
                  <h6 class="card-title">Manufacturer Details</h6>
                  <div class="table-responsive">
                    <table class="table table-sm">
                      <thead>
                        <tr>
                          <th>Manufacturer</th>
                          <th class="text-end">Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${insights.topManufacturers.data.labels.map((label, index) => `
                          <tr>
                            <td>${label}</td>
                            <td class="text-end">${formatCurrency(insights.topManufacturers.data.datasets[0].data[index])}</td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    if (insights.monthlySales?.data) {
      html += `
        <div class="tab-pane fade" id="monthly-sales-content" role="tabpanel"
             aria-labelledby="monthly-sales-tab">
          <div class="row">
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body" style="height: 400px;">
                  <h6 class="card-title">Monthly Sales Trend</h6>
                  <canvas id="monthlySalesChart" style="height: 350px;"></canvas>
                </div>
              </div>
            </div>
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body">
                  <h6 class="card-title">Monthly Sales Details</h6>
                  <div class="table-responsive">
                    <table class="table table-sm">
                      <thead>
                        <tr>
                          <th>Month</th>
                          <th class="text-end">Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${insights.monthlySales.data.labels.map((label, index) => `
                          <tr>
                            <td>${label}</td>
                            <td class="text-end">${formatCurrency(insights.monthlySales.data.datasets[0].data[index])}</td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    if (insights.yearlySales?.data) {
      html += `
        <div class="tab-pane fade" id="yearly-sales-content" role="tabpanel"
             aria-labelledby="yearly-sales-tab">
          <div class="row">
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body" style="height: 400px;">
                  <h6 class="card-title">10-Year Sales History</h6>
                  <canvas id="yearlySalesChart" style="height: 350px;"></canvas>
                </div>
              </div>
            </div>
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body">
                  <h6 class="card-title">Yearly Sales Details</h6>
                  <div class="table-responsive">
                    <table class="table table-sm">
                      <thead>
                        <tr>
                          <th>Year</th>
                          <th class="text-end">Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${insights.yearlySales.data.labels.map((label, index) => `
                          <tr>
                            <td>${label}</td>
                            <td class="text-end">${formatCurrency(insights.yearlySales.data.datasets[0].data[index])}</td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    html += `
        </div>
      </div>
    `;

    return html;
  };

  // Update the chart creation process for tab-based layout
  const originalShowActivity = window.showActivity;
  if (originalShowActivity) {
    window.showActivity = function(type, page = 1) {
      if (type === 'insights') {
        currentActivityType = type;

        // Update active state
        document.querySelectorAll('#activityList .list-group-item').forEach(item => {
          item.classList.remove('active');
          if (item.getAttribute('onclick').includes(`'${type}'`)) {
            item.classList.add('active');
          }
        });

        const contentDiv = document.querySelector('#activityContent .p-3');
        contentDiv.innerHTML = '<div class="text-center p-4"><div class="spinner-border text-primary" role="status"></div></div>';

        fetch(`/customers/${customerId}/activity/insights`)
          .then(response => response.json())
          .then(response => {
            console.log('Insights response:', response);
            contentDiv.innerHTML = renderInsights(response.data);

            // Create charts after tab is shown
            // First chart (top products - initially visible)
            if (response.data.topProducts?.data) {
              setTimeout(() => {
                const canvas = document.getElementById('topProductsChart');
                if (canvas) {
                  console.log('Creating top products chart');
                  ChartUtils.createChart(canvas, {
                    type: 'bar',
                    data: response.data.topProducts.data
                  });
                }
              }, 100);
            }

            // Set up event listeners for tab changes to create charts on demand
            const tabElements = document.querySelectorAll('[data-bs-toggle="tab"]');
            tabElements.forEach(tabElement => {
              tabElement.addEventListener('shown.bs.tab', function(event) {
                const targetId = event.target.getAttribute('id');

                // Create the appropriate chart based on which tab was clicked
                if (targetId === 'top-manufacturers-tab') {
                  const canvas = document.getElementById('topManufacturersChart');
                  if (canvas && response.data.topManufacturers?.data) {
                    console.log('Creating manufacturers chart');
                    ChartUtils.createChart(canvas, {
                      type: 'pie',
                      data: response.data.topManufacturers.data
                    });
                  }
                } else if (targetId === 'monthly-sales-tab') {
                  const canvas = document.getElementById('monthlySalesChart');
                  if (canvas && response.data.monthlySales?.data) {
                    console.log('Creating monthly sales chart');
                    ChartUtils.createChart(canvas, {
                      type: 'line',
                      data: response.data.monthlySales.data
                    });
                  }
                } else if (targetId === 'yearly-sales-tab') {
                  const canvas = document.getElementById('yearlySalesChart');
                  if (canvas && response.data.yearlySales?.data) {
                    console.log('Creating yearly sales chart');
                    ChartUtils.createChart(canvas, {
                      type: 'bar',
                      data: response.data.yearlySales.data
                    });
                  }
                }
              });
            });

          })
          .catch(error => {
            console.error('Insights error:', error);
            contentDiv.innerHTML = `
              <div class="alert alert-danger">
                Error loading insights: ${error}
              </div>
            `;
          });
      } else {
        // Call original function for other activity types
        originalShowActivity.call(this, type, page);
      }
    };

    console.log("Activity function overridden to use tabs");
  } else {
    console.warn("showActivity function not found");
  }

  console.log("Insights converted to tabs");

  // If you're already on the insights tab, reload it
  if (currentActivityType === 'insights') {
    showActivity('insights');
    console.log("Reloaded insights tab");
  }
}

// Call the function to convert insights to tabs
convertInsightsToTabs();
const ChartUtils = {
    // Updated color palette with pastel colors to match the new theme
    colors: {
        single: {
            primary: 'rgb(94, 188, 103)',     // primary green (unchanged)
            primaryDark: 'rgb(74, 150, 81)',  // darker shade for hover/emphasis
            secondary: 'rgb(121, 194, 255)',  // pastel blue
            accent: 'rgb(177, 156, 217)',     // pastel lavender
            peach: 'rgb(255, 185, 157)'       // pastel peach
        },
        palette: [
            'rgb(94, 188, 103)',     // primary green
            'rgb(121, 194, 255)',    // pastel blue colour
            'rgb(177, 156, 217)',    // pastel lavender
            'rgb(255, 185, 157)',    // pastel peach
            'rgb(134, 216, 201)',    // pastel mint
            'rgb(255, 223, 130)',    // pastel yellow
            'rgb(237, 174, 192)',    // pastel pink
            'rgb(160, 207, 182)'     // pastel sage
        ],
        // Transparency variants for fills
        transparentPalette: [
            'rgba(94, 188, 103, 0.2)',    // primary green with transparency
            'rgba(121, 194, 255, 0.2)',   // pastel blue with transparency
            'rgba(177, 156, 217, 0.2)',   // pastel lavender with transparency
            'rgba(255, 185, 157, 0.2)',   // pastel peach with transparency
            'rgba(134, 216, 201, 0.2)',   // pastel mint with transparency
            'rgba(255, 223, 130, 0.2)',   // pastel yellow with transparency
            'rgba(237, 174, 192, 0.2)',   // pastel pink with transparency
            'rgba(160, 207, 182, 0.2)'    // pastel sage with transparency
        ],
        // Gradient definitions with pastel colors
        gradients: {
            primary: (ctx, chartArea) => {
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(94, 188, 103, 0.1)');
                gradient.addColorStop(1, 'rgba(94, 188, 103, 0.6)');
                return gradient;
            },
            blue: (ctx, chartArea) => {
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(121, 194, 255, 0.1)');
                gradient.addColorStop(1, 'rgba(121, 194, 255, 0.6)');
                return gradient;
            },
            lavender: (ctx, chartArea) => {
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(177, 156, 217, 0.1)');
                gradient.addColorStop(1, 'rgba(177, 156, 217, 0.6)');
                return gradient;
            },
            peach: (ctx, chartArea) => {
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(255, 185, 157, 0.1)');
                gradient.addColorStop(1, 'rgba(255, 185, 157, 0.6)');
                return gradient;
            }
        }
    },

    validChartTypes: ['bar', 'line', 'pie', 'doughnut', 'scatter', 'radar', 'polarArea'],

    chartInstances: {},

    createChart(canvas, config) {
        if (!canvas || !config.data) {
            throw new Error("Canvas or data not provided");
        }

        // Ensure we have a unique chartId based on the canvas ID
        const chartId = canvas.id || `chart-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

        // If we already have a chart instance for this canvas ID, destroy it first
        if (this.chartInstances[chartId]) {
            this.chartInstances[chartId].destroy();
            delete this.chartInstances[chartId];
        }

        // Also check if any charts are using the same canvas element
        for (const id in this.chartInstances) {
            if (this.chartInstances[id].canvas === canvas) {
                this.chartInstances[id].destroy();
                delete this.chartInstances[id];
            }
        }

        const chartType = this.validateChartType(config.type);
        const datasets = this.createDatasets(config.data, chartType, canvas);
        const labels = config.data.labels;

        const chartConfig = {
            type: chartType,
            data: {
                labels: labels,
                datasets: datasets
            },
            options: this.createChartOptions(chartType, datasets, config.options)
        };

        const ctx = canvas.getContext('2d');
        this.chartInstances[chartId] = new Chart(ctx, chartConfig);

        // Store the canvas reference for this chart instance
        this.chartInstances[chartId]._canvasId = canvas.id;

        return this.chartInstances[chartId];
    },

    validateChartType(type) {
        const normalizedType = type.toLowerCase();
        if (!this.validChartTypes.includes(normalizedType)) {
            console.warn(`Invalid chart type "${type}". Falling back to "bar"`);
            return 'bar';
        }
        return normalizedType;
    },

    createDatasets(data, chartType, canvas) {
        const ctx = canvas.getContext('2d');
        const chartArea = {
            top: 0,
            bottom: canvas.height
        };

        if (['pie', 'doughnut', 'polarArea'].includes(chartType)) {
            return [{
                data: data.datasets[0].data,
                backgroundColor: data.datasets[0].data.map((_, index) =>
                    this.colors.palette[index % this.colors.palette.length]
                ),
                borderColor: 'white',
                borderWidth: 1,
                hoverBorderWidth: 2,
                hoverBorderColor: 'white',
                borderRadius: 2
            }];
        }

        return data.datasets.map((dataset, index) => {
            const isFirstDataset = index === 0;

            // Map each dataset to a different pastel color
            let color, borderColor, backgroundColor;

            if (isFirstDataset) {
                // First dataset - primary green
                color = this.colors.single.primary;
                borderColor = color;

                if (chartType === 'line') {
                    // Line chart (dots only, subtle fill)
                    backgroundColor = 'rgba(94, 188, 103, 0.1)';
                } else {
                    // Bar chart with solid color
                    backgroundColor = color;
                }
            } else if (index === 1) {
                // Second dataset - pastel blue
                color = this.colors.single.secondary;
                borderColor = color;
                backgroundColor = 'rgba(121, 194, 255, 0.1)';
            } else if (index === 2) {
                // Third dataset - pastel lavender
                color = this.colors.single.accent;
                borderColor = color;
                backgroundColor = 'rgba(177, 156, 217, 0.1)';
            } else {
                // Additional datasets - use the pastel palette cyclically
                const paletteIndex = (index - 3) % this.colors.palette.length;
                color = this.colors.palette[paletteIndex];
                borderColor = color;
                backgroundColor = this.colors.transparentPalette[paletteIndex];
            }

            return {
                ...dataset,
                backgroundColor,
                borderColor,
                borderWidth: isFirstDataset && chartType === 'bar' ? 0 : 2, // No border for bars
                borderRadius: chartType === 'bar' ? 8 : 0,
                tension: 0.6,
                fill: chartType === 'line' && !isFirstDataset, // Only fill secondary dataset if line
                pointBackgroundColor: color,
                pointBorderColor: 'white',
                pointBorderWidth: 1.5,
                pointRadius: 3, // Smaller point radius
                pointHoverRadius: 5,
                pointHoverBackgroundColor: color,
                pointHoverBorderColor: 'white',
                pointHoverBorderWidth: 2,
                yAxisID: isFirstDataset ? 'y' : 'y2',
                barPercentage: 0.7, // Thinner bars
                categoryPercentage: 0.8,
                animation: {
                    delay: (context) => context.dataIndex * 50 + index * 100 // Staggered animation
                }
            };
        });
    },

    createChartOptions(chartType, datasets, customOptions = {}) {
        // Font settings
        const fontFamily = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
        const defaultFont = {
            family: fontFamily,
            size: 11,
            weight: '400'
        };

        const titleFont = {
            family: "'Outfit', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            size: 12,
            weight: '600'
        };

        const options = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            layout: {
                padding: {
                    left: 5,
                    right: 10,
                    top: 5,
                    bottom: 5
                }
            },
            animation: {
                duration: 800,
                easing: 'easeOutQuart'
            },
            elements: {
                line: {
                    tension: 0.6,
                    borderWidth: 2
                },
                point: {
                    radius: 3,
                    hitRadius: 8,
                    hoverRadius: 5
                },
                bar: {
                    borderWidth: 0
                }
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    align: 'end',
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 10,
                        color: '#333340',
                        font: defaultFont,
                        boxWidth: 8
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    titleColor: '#333340',
                    bodyColor: '#333340',
                    borderColor: 'rgba(94, 188, 103, 0.3)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    boxShadow: '0px 2px 8px rgba(0, 0, 0, 0.1)',
                    usePointStyle: true,
                    titleFont: {
                        ...titleFont,
                        weight: '600'
                    },
                    bodyFont: defaultFont,
                    callbacks: {
                        // Custom label formatting if needed
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (context.parsed.y !== null) {
                                label += new Intl.NumberFormat('en-US', {
                                    style: 'decimal',
                                    maximumFractionDigits: 2
                                }).format(context.parsed.y);
                            }
                            return label;
                        }
                    }
                },
                datalabels: {
                    display: ['pie', 'doughnut', 'polarArea'].includes(chartType),
                    color: 'white',
                    font: {
                        ...defaultFont,
                        weight: '600'
                    }
                }
            },
            // Conditional scales based on chart type
            scales: ['pie', 'doughnut', 'polarArea'].includes(chartType) ? undefined : {
                x: {
                    grid: {
                        color: 'rgba(0, 0, 0, 0.03)',
                        drawBorder: false,
                        display: false
                    },
                    ticks: {
                        color: '#6c757d',
                        font: defaultFont,
                        maxRotation: 0,
                        autoSkip: true,
                        padding: 5
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.03)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#6c757d',
                        font: defaultFont,
                        padding: 5,
                        maxTicksLimit: 6,
                        callback: function(value) {
                            if (Math.abs(value) >= 1000000) {
                                return (value / 1000000).toFixed(1) + 'M';
                            } else if (Math.abs(value) >= 1000) {
                                return (value / 1000).toFixed(0) + 'k';
                            }
                            return value;
                        }
                    },
                    title: {
                        display: false,
                        text: datasets.length > 0 ? datasets[0].label : '',
                        color: '#333340',
                        font: titleFont
                    }
                },
                y2: {
                    type: 'linear',
                    display: datasets.length > 1,
                    position: 'right',
                    beginAtZero: true,
                    grid: {
                        drawOnChartArea: false,
                        drawBorder: false
                    },
                    ticks: {
                        color: '#6c757d',
                        font: defaultFont,
                        padding: 10
                    },
                    title: {
                        display: datasets.length > 1 && datasets[1].label ? true : false,
                        text: datasets.length > 1 ? datasets[1].label : '',
                        color: '#333340',
                        font: titleFont
                    }
                }
            }
        };

        // Properly merge in any custom options provided by the user
        return this._deepMerge(options, customOptions);
    },

    // Helper for deep merging objects
    _deepMerge(target, source) {
        const output = Object.assign({}, target);

        if (this._isObject(target) && this._isObject(source)) {
            Object.keys(source).forEach(key => {
                if (this._isObject(source[key])) {
                    if (!(key in target)) {
                        Object.assign(output, { [key]: source[key] });
                    } else {
                        output[key] = this._deepMerge(target[key], source[key]);
                    }
                } else {
                    Object.assign(output, { [key]: source[key] });
                }
            });
        }

        return output;
    },

    _isObject(item) {
        return (item && typeof item === 'object' && !Array.isArray(item));
    },

    updateChart(chartId, newData, newOptions = {}) {
        const chart = this.chartInstances[chartId];
        if (!chart) return;

        // Apply new data if provided
        if (newData) {
            if (newData.datasets) {
                // Re-apply styling to ensure consistency
                const canvas = chart.canvas;
                newData.datasets = this.createDatasets(newData, chart.config.type, canvas);
            }
            chart.data = newData;
        }

        // Apply new options if provided
        if (newOptions && Object.keys(newOptions).length > 0) {
            chart.options = this._deepMerge(chart.options, newOptions);
        }

        chart.update();
    },

    // Animation with softer transitions for pastel theme
    applyAnimation(chartId, animationType = 'fadeIn') {
        const chart = this.chartInstances[chartId];
        if (!chart) return;

        const animations = {
            fadeIn: {
                duration: 1200,  // Slightly longer for softer effect
                easing: 'easeOutQuad',
                from: 0,
                to: 1,
                loop: false
            },
            pulse: {
                duration: 1500,  // Slower pulse for gentler effect
                easing: 'easeInOutQuad',
                from: 0.85,
                to: 1.15,  // Less dramatic pulse
                loop: true
            },
            bounce: {
                duration: 1000,
                easing: 'easeOutBounce',
                from: 0.9,  // Less extreme bounce
                to: 1,
                loop: false
            }
        };

        const animation = animations[animationType] || animations.fadeIn;

        // Apply animation to all datasets
        chart.data.datasets.forEach((dataset, i) => {
            dataset.animation = animation;
        });

        chart.update();
    },

    // Highlight specific data points or bars with pastel highlight color
    highlightDataPoint(chartId, dataIndex, datasetIndex = 0) {
        const chart = this.chartInstances[chartId];
        if (!chart) return;

        // Reset all elements first
        chart.data.datasets.forEach(dataset => {
            if (dataset.backgroundColor instanceof Array) {
                dataset.backgroundColor.fill(dataset.originalBackgroundColor || dataset.backgroundColor[0]);
            }
            if (dataset.borderColor instanceof Array) {
                dataset.borderColor.fill(dataset.originalBorderColor || dataset.borderColor[0]);
            }
        });

        // Set highlight colors for the specific point
        const dataset = chart.data.datasets[datasetIndex];

        if (dataset) {
            if (!dataset.originalBackgroundColor) {
                dataset.originalBackgroundColor = dataset.backgroundColor;
            }

            if (dataset.backgroundColor instanceof Array) {
                // Use the peach color for highlights - stands out nicely against other pastels
                dataset.backgroundColor[dataIndex] = this.colors.single.peach;
            }

            if (dataset.borderColor instanceof Array) {
                dataset.borderColor[dataIndex] = this.colors.single.peach;
            }
        }

        chart.update();
    },

    // Toggle dark mode with updated pastel-compatible colors
    toggleDarkMode(chartId, isDark = false) {
        const chart = this.chartInstances[chartId];
        if (!chart) return;

        const darkTheme = {
            plugins: {
                legend: {
                    labels: {
                        color: '#e9ecef'
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(32, 33, 36, 0.95)',
                    titleColor: '#ffffff',
                    bodyColor: '#e9ecef',
                    borderColor: 'rgba(94, 188, 103, 0.5)'
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    },
                    ticks: {
                        color: '#adb5bd'
                    }
                },
                y: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    },
                    ticks: {
                        color: '#adb5bd'
                    },
                    title: {
                        color: '#e9ecef'
                    }
                },
                y2: {
                    ticks: {
                        color: '#adb5bd'
                    },
                    title: {
                        color: '#e9ecef'
                    }
                }
            }
        };

        const lightTheme = {
            plugins: {
                legend: {
                    labels: {
                        color: '#333340'
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    titleColor: '#333340',
                    bodyColor: '#333340',
                    borderColor: 'rgba(94, 188, 103, 0.3)'
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(0, 0, 0, 0.03)'
                    },
                    ticks: {
                        color: '#6c757d'
                    }
                },
                y: {
                    grid: {
                        color: 'rgba(0, 0, 0, 0.03)'
                    },
                    ticks: {
                        color: '#6c757d'
                    },
                    title: {
                        color: '#333340'
                    }
                },
                y2: {
                    ticks: {
                        color: '#6c757d'
                    },
                    title: {
                        color: '#333340'
                    }
                }
            }
        };

        const theme = isDark ? darkTheme : lightTheme;
        chart.options = this._deepMerge(chart.options, theme);
        chart.update();
    },

    destroyChart(chartId) {
        if (this.chartInstances[chartId]) {
            this.chartInstances[chartId].destroy();
            delete this.chartInstances[chartId];
        }
    },

    destroyAllCharts() {
        Object.keys(this.chartInstances).forEach(this.destroyChart.bind(this));
    },

    // Export chart as image
    exportChart(chartId, format = 'png', quality = 1.0) {
        const chart = this.chartInstances[chartId];
        if (!chart) return null;

        return chart.toBase64Image(format, quality);
    }
};

export default ChartUtils;
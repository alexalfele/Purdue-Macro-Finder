document.addEventListener('DOMContentLoaded', () => {
    // --- 1. GET REFERENCES TO HTML ELEMENTS ---
    
    // API URL - *** THIS IS THE FIX ***
    // Point to your live Render backend URL
    const API_BASE_URL = "https://purdue-macro-finder.onrender.com";
    
    // Tabs
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');
    
    // Forms
    const aiForm = document.getElementById('ai-form');
    const manualForm = document.getElementById('manual-form');
    
    // AI Form Inputs
    const aiCourt = document.getElementById('ai-court');
    const aiMeal = document.getElementById('ai-meal');
    
    // Manual Form Inputs
    const targetProtein = document.getElementById('target-protein');
    const targetCarbs = document.getElementById('target-carbs');
    const targetFat = document.getElementById('target-fat');
    const filterCheckboxes = document.querySelectorAll('.filters input[data-filter]');
    const mealPeriodCheckboxes = document.querySelectorAll('input[name="meal_period"]');

    // Buttons & Status
    const generateButton = document.getElementById('generate-button');
    const resetButton = document.getElementById('reset-button');
    const statusLabel = document.getElementById('status-label');
    
    // Results Panel
    const resultsPlaceholder = document.getElementById('results-placeholder');
    const loadingSpinner = document.getElementById('loading-spinner');
    const resultsContent = document.getElementById('results-content');
    
    // Result Content Parts
    const resultHeader = document.getElementById('result-header');
    const aiExplanation = document.getElementById('ai-explanation');
    const aiExplanationText = document.getElementById('ai-explanation-text');
    const chartCanvas = document.getElementById('macro-chart').getContext('2d');
    const mealPlanItems = document.getElementById('meal-plan-items');
    
    // --- 2. GLOBAL STATE VARIABLES ---
    let activeTab = 'ai'; // 'ai' or 'manual'
    let macroChart = null;
    let currentRetryTimeout = null; // For 503 errors

    // --- 3. EVENT LISTENERS ---

    // Tab switching
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            activeTab = button.dataset.tab;
            setActiveTab(activeTab);
        });
    });

    // Main "Generate" button (Critique #5)
    generateButton.addEventListener('click', () => {
        // Clear any pending retries
        if (currentRetryTimeout) clearTimeout(currentRetryTimeout);
        
        if (activeTab === 'ai') {
            handleAiSuggestion();
        } else {
            handleManualSearch();
        }
    });
    
    // Reset button
    resetButton.addEventListener('click', () => {
        aiForm.reset();
        manualForm.reset();
        // Re-check default meal periods
        document.querySelectorAll('input[name="meal_period"]').forEach(cb => {
            cb.checked = (cb.value === 'Lunch' || cb.value === 'Dinner');
        });
        showPlaceholder('Select a tab and generate a meal.');
        statusLabel.textContent = 'Cleared. Ready to go!';
    });

    // --- 4. CORE API FUNCTIONS ---

    async function handleAiSuggestion() {
        const payload = {
            court: aiCourt.value,
            meal: aiMeal.value,
        };
        
        // Show loading spinner (Critique #3)
        showLoadingState('Asking AI for a smart suggestion...');
        
        try {
            const result = await makeApiCall('/api/suggest_meal', payload);
            displayResult(result, true); // true = is AI result
        } catch (error) {
            handleApiError(error);
        }
    }

    async function handleManualSearch() {
        // Get selected meal periods
        const selectedMeals = [];
        mealPeriodCheckboxes.forEach(cb => {
            if (cb.checked) {
                selectedMeals.push(cb.value);
            }
        });

        // Get dietary filters
        const selectedFilters = {};
        filterCheckboxes.forEach(cb => {
            if (cb.checked) {
                selectedFilters[cb.dataset.filter] = true;
            }
        });

        const payload = {
            targets: {
                p: parseInt(targetProtein.value) || 0,
                c: parseInt(targetCarbs.value) || 0,
                f: parseInt(targetFat.value) || 0,
            },
            meal_periods: selectedMeals,
            dietary_filters: selectedFilters,
            exclusion_list: [] // You can add this feature later
        };
        
        // Show loading spinner
        showLoadingState('Calculating the best meal plan...');

        try {
            const result = await makeApiCall('/api/find_meal', payload);
            displayResult(result, false); // false = not AI result
        } catch (error) {
            handleApiError(error);
        }
    }
    
    async function makeApiCall(endpoint, payload) {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!response.ok) {
            // Check for our "still loading" error
            if (response.status === 503) {
                throw new Error('503:still_loading');
            }
            // Use the error message from the JSON body
            throw new Error(data.error || `HTTP error! Status: ${response.status}`);
        }
        
        // This should not happen with our app.py fix, but good to keep
        if (data.error) {
            throw new Error(data.error);
        }
        
        return data;
    }

    // --- 5. UI/DOM HELPER FUNCTIONS ---

    function setActiveTab(tabName) {
        tabButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });
        tabContents.forEach(content => {
            content.classList.toggle('active', content.id === tabName);
        });
        statusLabel.textContent = `Switched to ${tabName.toUpperCase()} tab.`;
    }

    function showLoadingState(message) {
        generateButton.disabled = true;
        generateButton.textContent = 'Finding...';
        statusLabel.textContent = message;
        
        resultsPlaceholder.classList.add('hidden');
        resultsContent.classList.add('hidden');
        loadingSpinner.classList.remove('hidden'); // Show spinner
    }

    function showPlaceholder(message) {
        statusLabel.textContent = message;
        resultsPlaceholder.classList.remove('hidden');
        resultsContent.classList.add('hidden');
        loadingSpinner.classList.add('hidden');
        generateButton.disabled = false;
        generateButton.textContent = 'Generate My Meal Plan 🍽️';
    }
    
    function handleApiError(error) {
        // Handle the 503 retry logic
        if (error.message === '503:still_loading') {
            statusLabel.textContent = 'Server is waking up. Retrying in 5s...';
            // Try again after 5 seconds
            currentRetryTimeout = setTimeout(() => {
                generateButton.click(); // Re-click the button
            }, 5000);
            // Keep loading state active
            return;
        }
        
        // Show a normal error
        showPlaceholder(`Error: ${error.message}`);
        statusLabel.textContent = `Error: ${error.message}`;
    }

    // This function builds the entire results panel (Critique #6)
    function displayResult(result, isAiResult) {
        statusLabel.textContent = `Found a meal at ${result.court}!`;
        
        // 1. Set Header
        resultHeader.textContent = isAiResult ? `AI Suggestion for ${result.meal_name}` : `Your Meal Plan for ${result.meal_name}`;
        
        // 2. Show/Hide AI Explanation Card
        if (isAiResult && result.explanation) {
            aiExplanationText.textContent = result.explanation;
            aiExplanation.classList.remove('hidden');
        } else {
            aiExplanation.classList.add('hidden');
        }
        
        // 3. Update Chart
        updateMacroChart(result.totals); // FIX: Pass the whole totals object
        
        // 4. Build Item Cards (Critique #6)
        mealPlanItems.innerHTML = ''; // Clear old items
        result.plan.forEach(item => {
            const card = document.createElement('div');
            card.className = 'item-card';
            
            card.innerHTML = `
                <div class="item-info">
                    <h4>${item.name}</h4>
                    <p>${item.serving_size || ''}</p>
                </div>
                <div class="item-macros">
                    <span class="p">P: ${(item.p || 0).toFixed(0)}g</span>
                    <span class="c">C: ${(item.c || 0).toFixed(0)}g</span>
                    <span class="f">F: ${(item.f || 0).toFixed(0)}g</span>
                </div>
            `;
            mealPlanItems.appendChild(card);
        });

        // 5. Show the results
        resultsPlaceholder.classList.add('hidden');
        loadingSpinner.classList.add('hidden');
        resultsContent.classList.remove('hidden');
        
        generateButton.disabled = false;
        generateButton.textContent = 'Generate My Meal Plan 🍽️';
    }

    function updateMacroChart(totals) {
        // FIX: Destructure the properties from the totals object
        const { p = 0, c = 0, f = 0 } = totals;
        const calories = [p * 4, c * 4, f * 9];
        const labels = [`Protein (${p.toFixed(0)}g)`, `Carbs (${c.toFixed(0)}g)`, `Fat (${f.toFixed(0)}g)`];

        if (macroChart) {
            macroChart.destroy();
        }

        macroChart = new Chart(chartCanvas, {
            type: 'pie',
            data: {
                labels: labels,
                datasets: [{
                    data: calories,
                    backgroundColor: ['#6ee7b7', '#7dd3fc', '#fde047'],
                    borderColor: '#3f3f46', 
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { labels: { color: 'white', font: { size: 12 } } },
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                let value = context.parsed;
                                let total = context.chart.getDatasetMeta(0).total;
                                let percentage = ((value / total) * 100).toFixed(0);
                                return `${context.label}: ${percentage}%`;
                            }
                        }
                    }
                }
            }
        });
    }

    // --- 6. INITIALIZE ---
    setActiveTab(activeTab); // Set the default tab on load
    showPlaceholder('Select a tab and generate a meal.');

});

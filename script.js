document.addEventListener('DOMContentLoaded', () => {
    // --- 1. CONFIGURATION ---
    const API_BASE_URL = "https://purdue-macro-finder.onrender.com";
    const RETRY_DELAY = 5000; // 5 seconds
    const MAX_RETRIES = 3;
    
    // --- 2. GET REFERENCES TO HTML ELEMENTS ---
    
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
    
    // --- 3. GLOBAL STATE VARIABLES ---
    let activeTab = 'ai';
    let macroChart = null;
    let currentRetryTimeout = null;
    let retryCount = 0;

    // --- 4. UTILITY FUNCTIONS ---
    
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    function validateMacroInput(value, macroName) {
        const num = parseInt(value) || 0;
        if (num < 0) {
            return { valid: false, message: `${macroName} cannot be negative` };
        }
        if (num > 500) {
            return { valid: false, message: `${macroName} seems unreasonably high` };
        }
        return { valid: true };
    }

    // --- 5. INPUT VALIDATION ---
    
    const validateInputs = debounce(() => {
        if (activeTab === 'manual') {
            const protein = parseInt(targetProtein.value) || 0;
            const carbs = parseInt(targetCarbs.value) || 0;
            const fat = parseInt(targetFat.value) || 0;
            
            // Check for warnings
            if (protein > 100) {
                statusLabel.textContent = '⚠️ High protein target';
                statusLabel.style.color = '#fbbf24'; // yellow
            } else if (protein === 0 && carbs === 0 && fat === 0) {
                statusLabel.textContent = 'Set your macro targets';
                statusLabel.style.color = '#a1a1aa'; // default
            } else {
                statusLabel.textContent = 'Ready to generate';
                statusLabel.style.color = '#a1a1aa'; // default
            }
        }
    }, 500);
    
    targetProtein.addEventListener('input', validateInputs);
    targetCarbs.addEventListener('input', validateInputs);
    targetFat.addEventListener('input', validateInputs);

    // --- 6. EVENT LISTENERS ---

    // Tab switching
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            activeTab = button.dataset.tab;
            setActiveTab(activeTab);
        });
    });

    // Main "Generate" button
    generateButton.addEventListener('click', () => {
        // Clear any pending retries
        if (currentRetryTimeout) {
            clearTimeout(currentRetryTimeout);
            currentRetryTimeout = null;
        }
        retryCount = 0;
        
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
        mealPeriodCheckboxes.forEach(cb => {
            cb.checked = (cb.value === 'Lunch' || cb.value === 'Dinner');
        });
        
        // Destroy chart if exists
        if (macroChart) {
            macroChart.destroy();
            macroChart = null;
        }
        
        showPlaceholder('Select a tab and generate a meal.');
        statusLabel.textContent = 'Cleared. Ready to go!';
        statusLabel.style.color = '#a1a1aa';
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !generateButton.disabled && 
            document.activeElement.tagName !== 'INPUT') {
            generateButton.click();
        }
        if (e.key === 'Escape') {
            resetButton.click();
        }
    });

    // --- 7. CORE API FUNCTIONS ---

    async function handleAiSuggestion() {
        const payload = {
            court: aiCourt.value,
            meal: aiMeal.value,
        };
        
        showLoadingState('Asking AI for a smart suggestion...');
        
        try {
            const result = await makeApiCall('/api/suggest_meal', payload);
            displayResult(result, true);
            retryCount = 0;
        } catch (error) {
            handleApiError(error);
        }
    }

    async function handleManualSearch() {
        // Validate inputs
        const protein = parseInt(targetProtein.value) || 0;
        const carbs = parseInt(targetCarbs.value) || 0;
        const fat = parseInt(targetFat.value) || 0;
        
        const proteinValidation = validateMacroInput(protein, 'Protein');
        if (!proteinValidation.valid) {
            showPlaceholder(proteinValidation.message);
            return;
        }
        
        // Get selected meal periods
        const selectedMeals = [];
        mealPeriodCheckboxes.forEach(cb => {
            if (cb.checked) {
                selectedMeals.push(cb.value);
            }
        });
        
        if (selectedMeals.length === 0) {
            showPlaceholder('Please select at least one meal period');
            return;
        }

        // Get dietary filters
        const selectedFilters = {};
        filterCheckboxes.forEach(cb => {
            if (cb.checked) {
                selectedFilters[cb.dataset.filter] = true;
            }
        });

        const payload = {
            targets: { p: protein, c: carbs, f: fat },
            meal_periods: selectedMeals,
            dietary_filters: selectedFilters,
            exclusion_list: []
        };
        
        showLoadingState('Calculating the best meal plan...');

        try {
            const result = await makeApiCall('/api/find_meal', payload);
            displayResult(result, false);
            retryCount = 0;
        } catch (error) {
            handleApiError(error);
        }
    }
    
    async function makeApiCall(endpoint, payload) {
        try {
            const response = await fetch(`${API_BASE_URL}${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal: AbortSignal.timeout(30000) // 30 second timeout
            });

            const data = await response.json();

            if (!response.ok) {
                if (response.status === 503) {
                    throw new Error('503:still_loading');
                }
                if (response.status === 429) {
                    throw new Error('429:rate_limit');
                }
                throw new Error(data.error || `HTTP error! Status: ${response.status}`);
            }
            
            if (data.error) {
                throw new Error(data.error);
            }
            
            return data;
            
        } catch (error) {
            if (error.name === 'TimeoutError') {
                throw new Error('Request timed out. Please try again.');
            }
            throw error;
        }
    }

    // --- 8. UI/DOM HELPER FUNCTIONS ---

    function setActiveTab(tabName) {
        tabButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });
        tabContents.forEach(content => {
            content.classList.toggle('active', content.id === tabName);
        });
        statusLabel.textContent = `Switched to ${tabName.toUpperCase()} tab.`;
        statusLabel.style.color = '#a1a1aa';
    }

    function showLoadingState(message) {
        generateButton.disabled = true;
        generateButton.innerHTML = '<span class="spinner-inline"></span> Finding...';
        statusLabel.textContent = message;
        statusLabel.style.color = '#a1a1aa';
        
        resultsPlaceholder.classList.add('hidden');
        resultsContent.classList.add('hidden');
        loadingSpinner.classList.remove('hidden');
    }

    function showPlaceholder(message) {
        statusLabel.textContent = message;
        statusLabel.style.color = '#a1a1aa';
        resultsPlaceholder.classList.remove('hidden');
        resultsContent.classList.add('hidden');
        loadingSpinner.classList.add('hidden');
        generateButton.disabled = false;
        generateButton.innerHTML = 'Generate My Meal Plan 🍽️';
    }
    
    function handleApiError(error) {
        // Handle rate limiting
        if (error.message === '429:rate_limit') {
            showPlaceholder('⚠️ Too many requests. Please wait a minute and try again.');
            generateButton.disabled = true;
            setTimeout(() => {
                generateButton.disabled = false;
            }, 60000); // Re-enable after 1 minute
            return;
        }
        
        // Handle 503 retry logic
        if (error.message === '503:still_loading') {
            if (retryCount < MAX_RETRIES) {
                retryCount++;
                statusLabel.textContent = `Server is waking up... Retry ${retryCount}/${MAX_RETRIES} in 5s`;
                statusLabel.style.color = '#fbbf24'; // yellow
                
                currentRetryTimeout = setTimeout(() => {
                    generateButton.click();
                }, RETRY_DELAY);
                return;
            } else {
                showPlaceholder('Server is taking too long to respond. Please try again later.');
                return;
            }
        }
        
        // Show normal error
        showPlaceholder(`Error: ${error.message}`);
        statusLabel.style.color = '#ef4444'; // red
    }

    function displayResult(result, isAiResult) {
        statusLabel.textContent = `✅ Found a meal at ${result.court}!`;
        statusLabel.style.color = '#22c55e'; // green
        
        // 1. Set Header
        resultHeader.textContent = isAiResult 
            ? `AI Suggestion: ${result.meal_name}` 
            : `Your Meal Plan: ${result.meal_name}`;
        
        // 2. Show/Hide AI Explanation Card
        if (isAiResult && result.explanation) {
            aiExplanationText.textContent = result.explanation;
            aiExplanation.classList.remove('hidden');
        } else {
            aiExplanation.classList.add('hidden');
        }
        
        // 3. Update Chart
        updateMacroChart(result.totals);
        
        // 4. Build Item Cards
        mealPlanItems.innerHTML = '';
        result.plan.forEach(item => {
            const card = document.createElement('div');
            card.className = 'item-card';
            
            // Add animation
            card.style.opacity = '0';
            card.style.transform = 'translateY(10px)';
            
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
            
            // Trigger animation
            setTimeout(() => {
                card.style.transition = 'all 0.3s ease';
                card.style.opacity = '1';
                card.style.transform = 'translateY(0)';
            }, 50);
        });

        // 5. Show results
        resultsPlaceholder.classList.add('hidden');
        loadingSpinner.classList.add('hidden');
        resultsContent.classList.remove('hidden');
        
        generateButton.disabled = false;
        generateButton.innerHTML = 'Generate My Meal Plan 🍽️';
    }

    function updateMacroChart(totals) {
        const { p = 0, c = 0, f = 0 } = totals;
        const calories = [p * 4, c * 4, f * 9];
        const labels = [
            `Protein (${p.toFixed(0)}g)`, 
            `Carbs (${c.toFixed(0)}g)`, 
            `Fat (${f.toFixed(0)}g)`
        ];

        // Destroy existing chart
        if (macroChart) {
            macroChart.destroy();
            macroChart = null;
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
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        labels: {
                            color: 'white',
                            font: { size: 12 }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                let value = context.parsed;
                                let total = context.chart.getDatasetMeta(0).total;
                                let percentage = ((value / total) * 100).toFixed(0);
                                return `${context.label}: ${percentage}% of calories`;
                            }
                        }
                    }
                }
            }
        });
    }

    // --- 9. INITIALIZE ---
    setActiveTab(activeTab);
    showPlaceholder('Select a tab and generate a meal.');

    // Add accessibility attributes
    generateButton.setAttribute('aria-label', 'Generate meal plan');
    loadingSpinner.setAttribute('role', 'status');
    loadingSpinner.setAttribute('aria-live', 'polite');

    console.log('Purdue Macro Finder initialized successfully!');
});

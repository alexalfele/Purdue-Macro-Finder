import os
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from meal_finder_engine import MealFinder
import threading # Need to import threading

# --- 1. SETUP THE FLASK APP ---
app = Flask(__name__)
CORS(app) 

# --- 2. LAZY INITIALIZATION SETUP ---
# We use a global variable and a lock for thread-safe, one-time initialization.
# These are defined here, but not run until the first request.
meal_finder_engine = None
engine_lock = threading.Lock()

def cleanup_old_caches():
    """Cleans up old cache files from previous days."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Cleaning caches, keeping files for: {today_str}")
    # Use '.' to refer to the current directory
    for filename in os.listdir('.'): 
        is_menu_cache = filename.startswith("menu_cache_")
        is_ai_cache = filename.startswith("ai_cache_")
        
        if (is_menu_cache or is_ai_cache) and today_str not in filename:
            try: 
                os.remove(filename)
                print(f"Removed old cache: {filename}")
            except OSError as e: 
                print(f"Error removing old cache file {filename}: {e}")

def get_engine():
    """
    Initializes and returns the MealFinder engine.
    This is thread-safe and ensures it only runs once.
    """
    global meal_finder_engine # Use the global variable
    
    # Fast path: If engine is already initialized, just return it.
    if meal_finder_engine:
        return meal_finder_engine
    
    # Slow path: Engine not initialized. Acquire lock.
    with engine_lock:
        # Double-check: Another thread might have initialized it
        # while we were waiting for the lock.
        if meal_finder_engine:
            return meal_finder_engine
            
        # --- This code now runs only ONCE, on the first request ---
        print("FIRST REQUEST: Initializing MealFinder engine...")
        cleanup_old_caches()
        
        # Assign to the global variable
        meal_finder_engine = MealFinder()
        
        print("FIRST REQUEST: Starting background data loaders...")
        meal_finder_engine.start_background_loaders()
        print("FIRST REQUEST: Initialization complete. Engine is live.")
        # --- End of one-time initialization ---
        
        return meal_finder_engine

# --- 3. API ENDPOINTS ---
# All endpoints must now call get_engine() first.

@app.route("/api/find_meal", methods=["POST"])
def api_find_meal():
    engine = get_engine() # Get the initialized engine

    # Check if the engine's data is loaded yet
    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503
    
    try:
        data = request.json
        targets = data.get('targets', {})
        meal_periods = data.get('meal_periods', ['Lunch'])
        exclusion_list = data.get('exclusion_list', [])
        dietary_filters = data.get('dietary_filters', {})

        result = engine.find_best_meal(
            targets,
            meal_periods,
            exclusion_list,
            dietary_filters
        )
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/find_meal: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/top_foods", methods=["GET"])
def api_get_top_foods():
    engine = get_engine() # Get the initialized engine

    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503
        
    try:
        top_foods = engine.get_top_protein_foods()
        return jsonify(top_foods)
    except Exception as e:
        print(f"Error in /api/top_foods: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/suggest_meal", methods=["POST"])
def api_suggest_meal():
    engine = get_engine() # Get the initialized engine

    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503

    try:
        data = request.json
        court = data.get('court')
        meal = data.get('meal')
        
        if not court or not meal:
            return jsonify({"error": "Court and meal period are required."}), 400
            
        suggestion = engine.get_ai_suggestion(court, meal)
        
        # Check if the AI suggestion itself is still loading
        if isinstance(suggestion, dict) and suggestion.get("status") == "loading":
            return jsonify({"error": "AI suggestions are still being pre-loaded. Please try again in a moment."}), 503
            
        return jsonify(suggestion)
        
    except Exception as e:
        print(f"Error in /api/suggest_meal: {e}")
        return jsonify({"error": str(e)}), 500

# --- 4. START THE SERVER (for local development) ---
if __name__ == "__main__":
    # This block is NOT run by gunicorn, only when you run "python app.py"
    print("Starting Flask development server...")
    # For local dev, we call get_engine() once to start the loaders
    get_engine() 
    app.run(debug=False, port=int(os.environ.get("PORT", 5000)))

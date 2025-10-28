import os
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from meal_finder_engine import MealFinder
import threading

# --- 1. SETUP THE FLASK APP ---
# Serve files from a 'static' folder
app = Flask(__name__, static_folder='static')
CORS(app) 

# --- 2. LAZY INITIALIZATION SETUP ---
meal_finder_engine = None
engine_lock = threading.Lock()

def cleanup_old_caches():
    """Cleans up old cache files from previous days."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Cleaning caches, keeping files for: {today_str}")
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
    """Initializes and returns the MealFinder engine (thread-safe)."""
    global meal_finder_engine
    if meal_finder_engine:
        return meal_finder_engine
    
    with engine_lock:
        if meal_finder_engine:
            return meal_finder_engine
            
        print("FIRST REQUEST: Initializing MealFinder engine...")
        cleanup_old_caches()
        
        meal_finder_engine = MealFinder()
        
        print("FIRST REQUEST: Starting background data loaders...")
        meal_finder_engine.start_background_loaders()
        print("FIRST REQUEST: Initialization complete. Engine is live.")
        
        return meal_finder_engine

# --- 3. NEW: ROUTE TO SERVE THE FRONTEND ---
@app.route("/")
def serve_index():
    """Serves the index.html file from the static folder."""
    # This will send static/index.html
    return send_from_directory(app.static_folder, 'index.html')

@app.route("/<path:path>")
def serve_static_files(path):
    """Serves other static files (like css, js) from the static folder."""
    # This will send static/style.css, static/script.js, etc.
    return send_from_directory(app.static_folder, path)


# --- 4. API ENDPOINTS ---
@app.route("/api/find_meal", methods=["POST"])
def api_find_meal():
    engine = get_engine() 
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

@app.route("/api/suggest_meal", methods=["POST"])
def api_suggest_meal():
    engine = get_engine()
    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503

    try:
        data = request.json
        court = data.get('court')
        meal = data.get('meal')
        
        if not court or not meal:
            return jsonify({"error": "Court and meal period are required."}), 400
            
        suggestion = engine.get_ai_suggestion(court, meal)
        
        if isinstance(suggestion, dict) and suggestion.get("status") == "loading":
            return jsonify({"error": "AI suggestions are still being pre-loaded. Please try again in a moment."}), 503
            
        return jsonify(suggestion)
        
    except Exception as e:
        print(f"Error in /api/suggest_meal: {e}")
        return jsonify({"error": str(e)}), 500

# --- 5. START THE SERVER ---
if __name__ == "__main__":
    print("Starting Flask development server...")
    get_engine() 
    app.run(debug=False, port=int(os.environ.get("PORT", 5000)))

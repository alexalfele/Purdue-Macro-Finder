import os
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from meal_finder_engine import MealFinder # Import your class

# --- 1. SETUP THE FLASK APP ---
app = Flask(__name__)
# This is crucial: it allows your frontend (on a different "origin") to make requests
CORS(app) 

# --- 2. CLEANUP OLD CACHES (Your original function) ---
def cleanup_old_caches():
    today_str = datetime.now().strftime('%Y-%m-%d')
    # Use '.' to refer to the current directory
    for filename in os.listdir('.'): 
        if filename.startswith("menu_cache_") and today_str not in filename:
            try: 
                os.remove(filename)
                print(f"Removed old cache: {filename}")
            except OSError as e: 
                print(f"Error removing old cache file {filename}: {e}")

# --- 3. CREATE AND PRE-LOAD THE MEALFINDER ENGINE ---
# We do this once when the server starts
cleanup_old_caches()
print("Initializing MealFinder engine...")
meal_finder_engine = MealFinder()
print("Loading all menu data on startup. This may take a moment...")
meal_finder_engine._load_all_menu_data() # Load data once on start
print("Data loaded. Server is ready. 🚀")

# --- ADD THIS LINE ---
# Start the background thread to pre-load AI suggestions
meal_finder_engine.start_ai_suggestion_preloader()
# ---------------------

# --- 4. CREATE THE API ENDPOINT FOR FINDING MEALS ---
# This function will be called by your JavaScript frontend
@app.route("/api/find_meal", methods=["POST"])
def api_find_meal():
    try:
        # Get all the user inputs from the web request's JSON body
        data = request.json
        targets = data.get('targets', {})
        meal_periods = data.get('meal_periods', ['Lunch'])
        exclusion_list = data.get('exclusion_list', [])
        dietary_filters = data.get('dietary_filters', {})

        # Run your existing algorithm
        result = meal_finder_engine.find_best_meal(
            targets,
            meal_periods,
            exclusion_list,
            dietary_filters
        )
        
        # Send the result back to the browser as JSON
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/find_meal: {e}")
        return jsonify({"error": str(e)}), 500 # Send an error message

# --- 5. CREATE THE API ENDPOINT FOR TOP PROTEIN FOODS ---
@app.route("/api/top_foods", methods=["GET"])
def api_get_top_foods():
    try:
        top_foods = meal_finder_engine.get_top_protein_foods()
        return jsonify(top_foods)
    except Exception as e:
        print(f"Error in /api/top_foods: {e}")
        return jsonify({"error": str(e)}), 500

# --- 5b. CREATE THE API ENDPOINT FOR AI SUGGESTION ---
@app.route("/api/suggest_meal", methods=["POST"])
def api_suggest_meal():
    try:
        data = request.json
        court = data.get('court')
        meal = data.get('meal')
        
        if not court or not meal:
            return jsonify({"error": "Court and meal period are required."}), 400
            
        # This function now uses the cache!
        suggestion = meal_finder_engine.get_ai_suggestion(court, meal)
        return jsonify(suggestion)
        
    except Exception as e:
        print(f"Error in /api/suggest_meal: {e}")
        return jsonify({"error": str(e)}), 500

# --- 6. START THE SERVER ---
if __name__ == "__main__":
    # This modification allows Render to set the port
    # We also turn off debug mode for production
    app.run(debug=False, port=int(os.environ.get("PORT", 5000)))

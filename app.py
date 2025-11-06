import os
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from meal_finder_engine import MealFinder
from config import Config  # Import the Config class
import threading
import time 
import requests
import logging

# --- 1. SETUP THE FLASK APP ---
app = Flask(__name__)
CORS(app) 
logging.basicConfig(level=logging.INFO)

# --- 2. CONFIGURE RATE LIMITING ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per day", "20 per hour"], # Fallback
    storage_uri="memory://",
)

# Apply limits from config.py
limit_minute = f"{Config.RATE_LIMIT_PER_MINUTE} per minute"
limit_hour = f"{Config.RATE_LIMIT_PER_HOUR} per hour"
limit_day = f"{Config.RATE_LIMIT_PER_DAY} per day"

# --- 3. KEEP-ALIVE CONFIGURATION ---
KEEP_ALIVE_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
KEEP_ALIVE_INTERVAL = 840  # 14 minutes
ENABLE_KEEP_ALIVE = os.environ.get("ENABLE_KEEP_ALIVE", "true").lower() == "true"

def keep_alive_ping():
    """Pings the server periodically to prevent spin-down"""
    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)
        if KEEP_ALIVE_URL:
            try:
                requests.get(f"{KEEP_ALIVE_URL}/", timeout=10)
                app.logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Keep-alive ping successful")
            except Exception as e:
                app.logger.warning(f"[{datetime.now().strftime('%H:%M:%S')}] Keep-alive ping failed: {e}")

# --- 4. LAZY INITIALIZATION SETUP ---
meal_finder_engine = None
engine_lock = threading.Lock()

def cleanup_old_caches():
    """Cleans up old cache files from previous days."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    app.logger.info(f"Cleaning caches, keeping files for: {today_str}")
    for filename in os.listdir('.'): 
        is_menu_cache = filename.startswith(Config.CACHE_PREFIX_MENU)
        is_ai_cache = filename.startswith(Config.CACHE_PREFIX_AI)
        
        if (is_menu_cache or is_ai_cache) and today_str not in filename:
            try: 
                os.remove(filename)
                app.logger.info(f"Removed old cache: {filename}")
            except OSError as e: 
                app.logger.error(f"Error removing old cache file {filename}: {e}")

def get_engine():
    """Initializes and returns the MealFinder engine (thread-safe)."""
    global meal_finder_engine
    if meal_finder_engine:
        return meal_finder_engine
    
    with engine_lock:
        if meal_finder_engine:
            return meal_finder_engine
            
        app.logger.info("FIRST REQUEST: Initializing MealFinder engine...")
        cleanup_old_caches()
        
        meal_finder_engine = MealFinder()
        
        app.logger.info("FIRST REQUEST: Starting background data loaders...")
        meal_finder_engine.start_background_loaders()
        app.logger.info("FIRST REQUEST: Initialization complete. Engine is live.")
        
        return meal_finder_engine

# --- 5. INPUT VALIDATION FUNCTIONS ---

def validate_targets(targets):
    """Validates the macro targets dictionary."""
    required_macros = ['p', 'c', 'f']
    for macro in required_macros:
        if macro not in targets:
            return False, f"Missing required macro: {macro}"
        
        value = targets[macro]
        try:
            val = float(value)
            if not Config.MIN_MACRO_TARGET <= val <= Config.MAX_MACRO_TARGET:
                return False, f"Macro {macro} ({val}) is out of range. Must be between {Config.MIN_MACRO_TARGET} and {Config.MAX_MACRO_TARGET}."
        except (ValueError, TypeError):
            return False, f"Macro {macro} must be a valid number."
            
    return True, None

def validate_meal_periods(meal_periods):
    """Validates the list of meal periods."""
    if not meal_periods or not isinstance(meal_periods, list):
        return False, "Please select at least one meal period."
    
    for period in meal_periods:
        if period not in Config.MEAL_PERIODS:
            return False, f"Invalid meal period: {period}"
            
    return True, None

# --- 6. HEALTH CHECK ROUTE ---
@app.route("/")
def health_check():
    """A simple route to confirm the server is running."""
    return jsonify({"status": "healthy", "message": "Purdue Macro Finder API is running."})

# --- 7. API ENDPOINTS ---
@app.route("/api/find_meal", methods=["POST"])
@limiter.limit(limit_minute) # Apply rate limits
@limiter.limit(limit_hour)
@limiter.limit(limit_day)
def api_find_meal():
    engine = get_engine() 
    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503
    
    try:
        data = request.json
        targets = data.get('targets', {})
        meal_periods = data.get('meal_periods', []) 
        dietary_filters = data.get('dietary_filters', {})
        exclusion_list = data.get('exclusion_list', [])

        # --- SERVER-SIDE VALIDATION ---
        valid, error = validate_targets(targets)
        if not valid:
            return jsonify({"error": error}), 400
            
        valid, error = validate_meal_periods(meal_periods)
        if not valid:
            return jsonify({"error": error}), 400

        result = engine.find_best_meal(
            targets,
            meal_periods,
            exclusion_list,
            dietary_filters
        )
        
        if result is None:
            return jsonify({"error": "No meal plan found. Try adjusting your filters."}), 404

        return jsonify(result)
        
    except Exception as e:
        app.logger.error(f"Error in /api/find_meal: {e}")
        return jsonify({"error": "An internal error occurred."}), 500

# --- NEW /api/suggest_meal ---
@app.route("/api/suggest_meal", methods=["POST"])
@limiter.limit(limit_minute) # Apply rate limits
@limiter.limit(limit_hour)
@limiter.limit(limit_day)
def api_suggest_meal():
    engine = get_engine()
    if not engine.data_loaded:
        return jsonify({"error": "Server is still loading menu data. Please try again in a moment."}), 503

    try:
        data = request.json
        goal = data.get('goal')
        
        if not goal or len(goal) < 5:
            return jsonify({"error": "A descriptive goal is required."}), 400
            
        # 1. Ask AI to convert goal to macros
        app.logger.info(f"AI Goal received: '{goal}'")
        ai_result = engine.get_macros_from_ai(goal)
        
        if ai_result.get("error"):
            return jsonify({"error": ai_result.get("error")}), 500
            
        targets = ai_result.get("targets")
        ai_explanation = ai_result.get("explanation", "AI analyzed your goal.")
        
        app.logger.info(f"AI returned targets: {targets}")
        
        # 2. Validate the targets from the AI
        valid, error = validate_targets(targets)
        if not valid:
            app.logger.error(f"AI returned invalid targets: {error}")
            return jsonify({"error": "The AI provided an invalid target. Please rephrase your goal."}), 500
        
        # 3. Use the AI targets to run the *real* optimization
        # We'll default to Lunch and Dinner, the most common meals for goals.
        default_meal_periods = ["Lunch", "Dinner"]
        
        optimized_meal = engine.find_best_meal(
            targets=targets,
            meal_periods_to_check=default_meal_periods,
            exclusion_list=[],
            dietary_filters={} # You could add filters to the AI goal later!
        )
        
        if optimized_meal is None:
            return jsonify({"error": f"No meal plan found for your goal. AI set targets: P:{targets['p']} C:{targets['c']} F:{targets['f']}. Try a different goal."}), 404

        # 4. Add the AI explanation to the final result
        optimized_meal['ai_explanation'] = f"For your goal, I set targets of P:{targets['p']}g, C:{targets['c']}g, F:{targets['f']}g. {ai_explanation}"

        return jsonify(optimized_meal)
        
    except Exception as e:
        app.logger.error(f"Error in /api/suggest_meal: {e}")
        return jsonify({"error": "An internal error occurred."}), 500

# --- 8. START THE SERVER ---
if __name__ == "__main__":
    # Start keep-alive thread if enabled and URL is available
    if ENABLE_KEEP_ALIVE and KEEP_ALIVE_URL:
        keep_alive_thread = threading.Thread(
            target=keep_alive_ping,
            daemon=True,
            name="KeepAlive"
        )
        keep_alive_thread.start()
        app.logger.info(f"Keep-alive thread started (pinging every {KEEP_ALIVE_INTERVAL}s)")
    else:
        app.logger.info("Keep-alive disabled or URL not available")
    
    app.logger.info("Starting Flask development server...")
    get_engine() 
    # Use 0.0.0.0 to be accessible on the network
    app.run(host='0.0.0.0', debug=False, port=int(os.environ.get("PORT", 5000)))

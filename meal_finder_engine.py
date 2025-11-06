import threading
import requests
import json
import re
import random
import math
import os
import time
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from google import genai
from config import Config

# Use Flask's logger if available, or create a new one
logger = logging.getLogger(__name__)

class MealFinder:
    """
    Handles all backend operations: fetching data from the Purdue API,
    caching results, and running the algorithm to find optimal meal plans.
    """
    
    def __init__(self):
        """Initializes the MealFinder, setting up API endpoints and data structures."""
        self.url = Config.PURDUE_API_URL
        self.headers = {"Content-Type": "application/json"}
        self.query = """
        query GetMenu($courtName: String!, $date: Date!) {
          diningCourtByName(name: $courtName) {
            name
            dailyMenu(date: $date) {
              meals { name, stations { name, items { displayName, item { traits { name }, nutritionFacts { name, label } } } } }
            }
          }
        }
        """
        self.dining_courts = Config.DINING_COURTS
        
        # Date-dependent variables
        self.todays_date = datetime.now().strftime('%Y-%m-%d')
        self.cache_file = f"{Config.CACHE_PREFIX_MENU}{self.todays_date}.json"
        self.ai_cache_file = f"{Config.CACHE_PREFIX_AI}{self.todays_date}.json"
        
        # Data storage
        self.master_item_list = []
        self.data_loaded = False
        self.ai_suggestions_cache = {} # Will now store { "user goal string": {"p": 50, ...} }
        self.cache_lock = threading.Lock()
        
        # Performance indices
        self.items_by_court = {}  # court -> [items]
        self.items_by_meal = {}   # meal_name -> [items]
        
        # API key validation
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not found. AI suggestions will be disabled.")
        else:
            genai.configure(api_key=self.api_key)
            self.ai_model = genai.GenerativeModel(Config.GEMINI_MODEL)


    def _ensure_current_date(self):
        """Ensures all date-dependent variables are current."""
        current_date = datetime.now().strftime('%Y-%m-%d')
        if self.todays_date != current_date:
            logger.info(f"Date changed from {self.todays_date} to {current_date}. Refreshing...")
            self.todays_date = current_date
            self.cache_file = f"{Config.CACHE_PREFIX_MENU}{self.todays_date}.json"
            self.ai_cache_file = f"{Config.CACHE_PREFIX_AI}{self.todays_date}.json"
            self.master_item_list = []
            self.data_loaded = False
            self.items_by_court.clear()
            self.items_by_meal.clear()
            # Clear AI cache as well, as targets might be context-dependent (though unlikely)
            self.ai_suggestions_cache.clear()
            return True
        return False

    def _get_numeric_value(self, label_str):
        """Extracts a number from a string label (e.g., '15g' -> 15.0)."""
        if not label_str:
            return 0.0
        numeric_part = re.search(r'[\d.]+', label_str)
        return float(numeric_part.group(0)) if numeric_part else 0.0

    def _calculate_score(self, meal_plan, targets, weights, penalties):
        """
        Scores a meal plan based on its deviation from target macros.
        A lower score is better. Applies penalties for missing protein or exceeding carbs/fats.
        """
        if not meal_plan:
            return float('inf'), {}
        
        totals = {
            'p': sum(item.get('p', 0) for item in meal_plan),
            'c': sum(item.get('c', 0) for item in meal_plan),
            'f': sum(item.get('f', 0) for item in meal_plan)
        }
        
        errors = {
            'p': totals['p'] - targets['p'],
            'c': totals['c'] - targets['c'],
            'f': totals['f'] - targets['f']
        }
        
        # Apply penalties
        if errors['p'] < 0:
            errors['p'] *= penalties['under_p']
        if errors['c'] > 0:
            errors['c'] *= penalties['over_c']
        if errors['f'] > 0:
            errors['f'] *= penalties['over_f']
        
        score = (
            weights['p'] * (errors['p']**2) +
            weights['c'] * (errors['c']**2) +
            weights['f'] * (errors['f']**2)
        )**0.5
        
        return score, totals

    def _get_menu_data_for_court(self, court, cached_data):
        """Fetches menu data for a single dining court, using cache if available."""
        menu_data = cached_data.get(court)
        if menu_data:
            return court, menu_data, False
        
        try:
            variables = {"courtName": court, "date": self.todays_date}
            resp = requests.post(
                self.url,
                json={"query": self.query, "variables": variables},
                headers=self.headers,
                timeout=Config.API_TIMEOUT
            )
            resp.raise_for_status()
            return court, resp.json(), True
        except requests.exceptions.Timeout:
            logger.error(f"Timeout fetching menu for {court}")
            return court, None, False
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching menu for {court}: {e}")
            return court, None, False

    def _build_indices(self):
        """Build lookup indices after loading data for faster filtering."""
        self.items_by_court.clear()
        self.items_by_meal.clear()
        
        for item in self.master_item_list:
            court = item['court']
            meal = item['meal_name']
            
            if court not in self.items_by_court:
                self.items_by_court[court] = []
            self.items_by_court[court].append(item)
            
            if meal not in self.items_by_meal:
                self.items_by_meal[meal] = []
            self.items_by_meal[meal].append(item)
        
        logger.info(f"Built indices: {len(self.items_by_court)} courts, {len(self.items_by_meal)} meals")

    def _load_all_menu_data(self):
        """
        Loads menu data for all dining courts, using multithreading for speed.
        Manages a daily cache to avoid excessive API calls.
        """
        current_date_str = datetime.now().strftime('%Y-%m-%d')
        
        if self.data_loaded and self.todays_date == current_date_str:
            logger.info(f"Menu data for {current_date_str} is already loaded.")
            return
        
        if self.todays_date != current_date_str:
            self._ensure_current_date()
        
        logger.info(f"Starting menu data load for {self.todays_date}...")
        
        cached_data, needs_to_save_cache = {}, False
        
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cached_data = json.load(f).get("data", {})
                logger.info(f"Loaded {self.cache_file} from disk.")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading cache file: {e}")
        
        with ThreadPoolExecutor() as executor:
            future_to_court = {
                executor.submit(self._get_menu_data_for_court, court, cached_data): court
                for court in self.dining_courts
            }
            
            for future in future_to_court:
                court, menu_data, was_fetched = future.result()
                if was_fetched:
                    cached_data[court] = menu_data
                    needs_to_save_cache = True
                
                if menu_data and 'data' in menu_data:
                    dining_court = menu_data.get('data', {}).get('diningCourtByName')
                    if dining_court and dining_court.get('dailyMenu'):
                        for meal in dining_court['dailyMenu']['meals']:
                            for station in meal['stations']:
                                for item_appearance in station['items']:
                                    core_item = item_appearance.get('item')
                                    if core_item and core_item.get('nutritionFacts'):
                                        macros = {
                                            'Protein': 0,
                                            'Total Carbohydrate': 0,
                                            'Total fat': 0
                                        }
                                        serving_size = ""
                                        for fact in core_item['nutritionFacts']:
                                            if fact['name'] in macros:
                                                macros[fact['name']] = self._get_numeric_value(
                                                    fact.get('label')
                                                )
                                            elif fact['name'] == 'Serving Size':
                                                serving_size = fact.get('label', '')
                                        
                                        if sum(macros.values()) > 0:
                                            traits = [
                                                trait['name']
                                                for trait in core_item.get('traits', [])
                                                if trait
                                            ] if core_item.get('traits') else []
                                            
                                            self.master_item_list.append({
                                                "name": item_appearance['displayName'],
                                                "p": macros['Protein'],
                                                "c": macros['Total Carbohydrate'],
                                                "f": macros['Total fat'],
                                                "court": court,
                                                "meal_name": meal['name'],
                                                "traits": traits,
                                                "serving_size": serving_size
                                            })
        
        if needs_to_save_cache:
            try:
                with open(self.cache_file, 'w') as f:
                    json.dump({
                        "timestamp": datetime.now().isoformat(),
                        "data": cached_data
                    }, f)
                logger.info(f"Saved menu cache to {self.cache_file}")
            except IOError as e:
                logger.error(f"Error saving cache file: {e}")
        
        self._build_indices()
        self.data_loaded = True
        logger.info(f"Menu data load complete. {len(self.master_item_list)} items loaded.")

    # --- AI SUGGESTION METHODS (NEW) ---

    def _load_ai_cache_from_disk(self):
        """Loads the AI *target* cache from a JSON file."""
        self._ensure_current_date()
        
        if os.path.exists(self.ai_cache_file):
            logger.info(f"Loading AI target cache from {self.ai_cache_file}")
            try:
                with open(self.ai_cache_file, 'r') as f:
                    # The cache file just stores { "goal string": { "p": ... } }
                    data_from_disk = json.load(f)
                    with self.cache_lock:
                        self.ai_suggestions_cache = data_from_disk
                    logger.info(f"Loaded {len(self.ai_suggestions_cache)} cached AI targets")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to read AI cache: {e}")
                self.ai_suggestions_cache = {}

    def _save_ai_cache_to_disk(self):
        """Saves the current AI *target* cache to a JSON file."""
        try:
            with self.cache_lock:
                # The cache is already in a simple { "goal": { ... } } format
                data_to_save = self.ai_suggestions_cache
            
            with open(self.ai_cache_file, 'w') as f:
                json.dump(data_to_save, f)
            
            logger.debug(f"Saved {len(data_to_save)} AI targets to cache")
        except Exception as e:
            logger.error(f"Error saving AI cache: {e}")

    def _get_macros_from_ai_api(self, user_goal, is_retry=False):
        """Calls the Gemini API to convert a goal string into macro targets."""
        if not self.api_key:
            return {"error": "AI service is not configured."}
        
        prompt = f"""
You are a sports nutritionist. A student at Purdue University needs to find a meal.
Their goal is: "{user_goal}"

Your job is to convert this goal into a set of macro targets (protein, carbs, fat) for a single meal.

- Prioritize protein for all muscle gain or "high protein" requests (40-60g).
- Keep fat low (10-20g) unless they specifically ask for a keto or high-fat meal.
- "Pre-workout" should have high carbs (50-80g).
- "Post-workout" should have high protein (40-50g) and high carbs (50-70g).
- "Fat loss" or "low-carb" should have high protein (30-50g) and low carbs (10-30g).
- A "balanced" meal should be around 30p, 50c, 15f.

Return ONLY a valid JSON object with three keys: "p", "c", "f", and a short "explanation".

Example 1:
User Goal: "High protein meal for muscle gain"
Your Response:
{{
  "p": 50,
  "c": 40,
  "f": 15,
  "explanation": "Set a high protein target for muscle repair and balanced carbs/fats."
}}

Example 2:
User Goal: "a light, low-carb meal"
Your Response:
{{
  "p": 30,
  "c": 20,
  "f": 10,
  "explanation": "Set moderate protein with low carbs and fat for a light meal."
}}
"""
        
        try:
            response = self.ai_model.generate_content(prompt)
            
            clean_response = response.text.strip().lstrip("```json").rstrip("```")
            ai_data = json.loads(clean_response)
            
            # Validate the AI's output
            if 'p' not in ai_data or 'c' not in ai_data or 'f' not in ai_data:
                raise ValueError("AI response missing required p, c, or f keys.")
                
            targets = {
                "p": int(ai_data['p']),
                "c": int(ai_data['c']),
                "f": int(ai_data['f'])
            }
            
            explanation = ai_data.get("explanation", "AI analyzed your goal.")
            
            return { "targets": targets, "explanation": explanation }
        
        except Exception as e:
            if "429" in str(e) and not is_retry:
                logger.warning(f"Rate limit hit for AI goal: '{user_goal}', retrying...")
                delay = random.randint(Config.RETRY_DELAY_MIN, Config.RETRY_DELAY_MAX)
                time.sleep(delay)
                return self._get_macros_from_ai_api(user_goal, is_retry=True)
            
            logger.error(f"Gemini API error for goal '{user_goal}': {e}")
            if "response" in locals():
                logger.error(f"Raw AI Response: {response.text}")
            return {"error": "The AI failed to interpret your goal. Try rephrasing."}

    def get_macros_from_ai(self, user_goal):
        """Gets macro targets for a user goal, checking cache first."""
        if self._ensure_current_date():
            self._load_all_menu_data()
            while not self.data_loaded:
                time.sleep(0.5)
        
        # Normalize the goal string for better cache hits
        cache_key = user_goal.strip().lower()
        
        with self.cache_lock:
            if cache_key in self.ai_suggestions_cache:
                logger.info(f"AI target cache HIT for: '{user_goal}'")
                return self.ai_suggestions_cache[cache_key]
        
        # Cache miss, call the API
        logger.info(f"AI target cache MISS for: '{user_goal}'. Calling API...")
        result = self._get_macros_from_ai_api(user_goal)
        
        # Update cache if successful
        if "error" not in result:
            with self.cache_lock:
                self.ai_suggestions_cache[cache_key] = result
            self._save_ai_cache_to_disk()
        
        return result

    def start_background_loaders(self):
        """Starts background threads for menu and AI loading."""
        logger.info("Starting background loaders...")
        
        # Thread 1: Load menus (Always do this)
        menu_loader_thread = threading.Thread(
            target=self._load_all_menu_data,
            daemon=True,
            name="MenuLoader"
        )
        menu_loader_thread.start()
        
        # Thread 2: Load existing AI target cache from disk
        # We no longer pre-load, just load what's already saved.
        ai_cache_loader_thread = threading.Thread(
            target=self._load_ai_cache_from_disk,
            daemon=True,
            name="AICacheLoader"
        )
        ai_cache_loader_thread.start()
        logger.info("AI target cache loader started (on-demand mode).")


    # --- MEAL FINDING ALGORITHM ---

    def _run_optimization_for_court(self, available_items, targets, weights, penalties):
        """Runs simulated annealing for a single court's items."""
        if len(available_items) < Config.MIN_ITEMS:
            return None, float('inf'), {}
        
        best_solution, best_score, best_totals = None, float('inf'), {}
        temp = Config.INITIAL_TEMP
        cooling_rate = Config.COOLING_RATE
        iterations = Config.ITERATIONS
        
        initial_size = min(Config.INITIAL_ITEMS, len(available_items))
        if not available_items or len(available_items) < initial_size:
            return None, float('inf'), {} # Not enough items
            
        current_solution = random.sample(available_items, initial_size)
        
        for _ in range(iterations):
            if temp <= 1:
                break
            
            neighbor = list(current_solution)
            
            action = random.choice(['swap', 'add', 'remove'])
            
            if action == 'swap' and len(neighbor) > 1:
                neighbor[random.randrange(len(neighbor))] = random.choice(available_items)
            
            elif action == 'add' and len(neighbor) < Config.MAX_ITEMS:
                possible_adds = [i for i in available_items if i not in neighbor]
                if possible_adds:
                    neighbor.append(random.choice(possible_adds))
            
            elif action == 'remove' and len(neighbor) > Config.MIN_ITEMS:
                neighbor.pop(random.randrange(len(neighbor)))
            
            current_score, _ = self._calculate_score(current_solution, targets, weights, penalties)
            neighbor_score, neighbor_totals = self._calculate_score(neighbor, targets, weights, penalties)
            
            if neighbor_score < current_score or random.random() < math.exp((current_score - neighbor_score) / temp):
                current_solution = neighbor
            
            if neighbor_score < best_score:
                best_score = neighbor_score
                best_totals = neighbor_totals
                best_solution = neighbor
            
            temp *= cooling_rate
        
        return best_solution, best_score, best_totals

    def find_best_meal(self, targets, meal_periods_to_check, exclusion_list=None, dietary_filters=None):
        """Finds the best meal plan across all dining courts."""
        if exclusion_list is None:
            exclusion_list = []
        if dietary_filters is None:
            dietary_filters = {}
        
        if self._ensure_current_date():
            self._load_all_menu_data()
        
        if not self.data_loaded:
            logger.warning("find_best_meal called before data was loaded.")
            return None
        
        filtered_master_list = []
        for item in self.master_item_list:
            traits = item.get('traits', [])
            passes_filter = True
            
            if dietary_filters.get("Vegetarian") and "Vegetarian" not in traits:
                passes_filter = False
            if dietary_filters.get("Vegan") and "Vegan" not in traits:
                passes_filter = False
            if dietary_filters.get("No Gluten") and "Contains Gluten" in traits:
                passes_filter = False
            
            if passes_filter:
                filtered_master_list.append(item)
        
        available_courts = set(
            item['court'] for item in filtered_master_list
            if item['name'] not in exclusion_list and item['meal_name'] in meal_periods_to_check
        )
        
        if not available_courts:
            logger.warning(f"No courts available for filters/meals: {meal_periods_to_check}")
            return None
        
        overall_best_solution, overall_best_score = None, float('inf')
        weights = Config.WEIGHTS
        penalties = Config.PENALTIES
        
        for court in available_courts:
            court_specific_items = [
                item for item in filtered_master_list
                if item['court'] == court and
                   item['name'] not in exclusion_list and
                   item['meal_name'] in meal_periods_to_check
            ]
            
            court_solution, court_score, court_totals = self._run_optimization_for_court(
                court_specific_items, targets, weights, penalties
            )
            
            if court_solution and court_score < overall_best_score:
                overall_best_score = court_score
                overall_best_solution = {
                    "score": court_score,
                    "court": court,
                    "meal_name": court_solution[0]['meal_name'],
                    "plan": court_solution,
                    "totals": court_totals
                }
        
        return overall_best_solution

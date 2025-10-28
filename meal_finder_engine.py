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
        self.ai_suggestions_cache = {}
        self.cache_lock = threading.Lock()
        
        # Performance indices
        self.items_by_court = {}  # court -> [items]
        self.items_by_meal = {}   # meal_name -> [items]
        
        # API key validation
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not found. AI suggestions will be disabled.")

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
        # Refresh date on every load
        current_date_str = datetime.now().strftime('%Y-%m-%d')
        
        if self.data_loaded and self.todays_date == current_date_str:
            logger.info(f"Menu data for {current_date_str} is already loaded.")
            return
        
        # Update dates if stale
        if self.todays_date != current_date_str:
            self._ensure_current_date()
        
        logger.info(f"Starting menu data load for {self.todays_date}...")
        
        cached_data, needs_to_save_cache = {}, False
        
        # Load from cache if exists
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cached_data = json.load(f).get("data", {})
                logger.info(f"Loaded {self.cache_file} from disk.")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading cache file: {e}")
        
        # Fetch data for all courts in parallel
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
                
                # Parse menu data
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
                                        
                                        # Only add items with nutritional data
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
        
        # Save cache if updated
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
        
        # Build performance indices
        self._build_indices()
        
        # Mark as loaded
        self.data_loaded = True
        logger.info(f"Menu data load complete. {len(self.master_item_list)} items loaded.")

    def get_top_protein_foods(self, count=25):
        """Returns the top protein-dense foods."""
        unique_foods = {
            (item['name'], item['p'], item['c'], item['f']): item
            for item in self.master_item_list
        }
        
        protein_dense_foods = []
        for food in unique_foods.values():
            calories = (food.get('p', 0) * 4) + (food.get('c', 0) * 4) + (food.get('f', 0) * 9)
            if calories > 50 and food.get('p', 0) > 5:
                protein_per_100kcal = (food.get('p', 0) / calories) * 100
                protein_dense_foods.append({
                    **food,
                    "calories": calories,
                    "protein_density": protein_per_100kcal
                })
        
        protein_dense_foods.sort(key=lambda x: x['protein_density'], reverse=True)
        return protein_dense_foods[:count]

    # --- AI SUGGESTION METHODS ---

    def _load_ai_cache_from_disk(self):
        """Loads the AI suggestion cache from a JSON file."""
        self._ensure_current_date()
        
        if os.path.exists(self.ai_cache_file):
            logger.info(f"Loading AI cache from {self.ai_cache_file}")
            try:
                with open(self.ai_cache_file, 'r') as f:
                    data_from_disk = json.load(f)
                    with self.cache_lock:
                        # Convert list keys back to tuples
                        self.ai_suggestions_cache = {
                            tuple(k): v for k, v in data_from_disk.items()
                        }
                    logger.info(f"Loaded {len(self.ai_suggestions_cache)} AI suggestions")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to read AI cache: {e}")
                self.ai_suggestions_cache = {}

    def _save_ai_cache_to_disk(self):
        """Saves the current AI suggestion cache to a JSON file."""
        try:
            with self.cache_lock:
                # Convert tuple keys to lists for JSON
                data_to_save = {list(k): v for k, v in self.ai_suggestions_cache.items()}
            
            with open(self.ai_cache_file, 'w') as f:
                json.dump(data_to_save, f)
            
            logger.debug(f"Saved {len(data_to_save)} AI suggestions to cache")
        except Exception as e:
            logger.error(f"Error saving AI cache: {e}")

    def _fetch_ai_suggestion_from_api(self, court_name, meal_name, is_retry=False):
        """Calls the Gemini API to generate a meal suggestion."""
        if not self.api_key:
            return {"error": "AI service is not configured."}
        
        # Get available items using index for better performance
        available_items = [
            item for item in self.master_item_list
            if item['court'] == court_name and item['meal_name'] == meal_name
        ]
        
        if not available_items:
            return {"error": "No items found for this dining court and meal."}
        
        # Build food list string
        food_list_str = "\n".join([
            f"- {item['name']} (P:{item.get('p', 0)}g, C:{item.get('c', 0)}g, F:{item.get('f', 0)}g)"
            for item in available_items
        ])
        
        prompt = f"""
You are a Purdue University dining hall nutritionist. 
Your goal is to help a student pick a balanced, healthy, and protein-rich meal
from the {court_name} dining court for {meal_name}.

Here is the full list of available foods:
{food_list_str}

Please select 3-5 items that make a healthy, balanced meal. 
Prioritize a lean protein, a vegetable or fruit, and a whole-grain carb.

Return your answer as ONLY a valid JSON object with two keys:
1. "foods": A JSON list of the exact food names.
2. "explanation": A brief 1-2 sentence reason for why this meal is a good choice.

Example:
{{
  "foods": ["Grilled Chicken Breast", "Steamed Broccoli", "Brown Rice"],
  "explanation": "This meal provides lean protein from the chicken, fiber and vitamins from the broccoli, and complex carbs from the brown rice for sustained energy."
}}
"""
        
        try:
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=f"models/{Config.GEMINI_MODEL}",
                contents=prompt
            )
            
            # Parse response
            clean_response = response.text.strip().replace("```json", "").replace("```", "")
            ai_data = json.loads(clean_response)
            
            suggested_names = ai_data.get("foods", [])
            explanation = ai_data.get("explanation", "No explanation provided by AI.")
            
            if not suggested_names:
                return {"error": "AI could not find a valid combination."}
            
            # Build suggestion from available items
            suggestion = []
            totals_map = {'p': 0, 'c': 0, 'f': 0}
            
            for name in suggested_names:
                for item in available_items:
                    if item['name'] == name:
                        suggestion.append(item)
                        totals_map['p'] += item.get('p', 0)
                        totals_map['c'] += item.get('c', 0)
                        totals_map['f'] += item.get('f', 0)
                        break
            
            if not suggestion:
                return {"error": "AI could not find a valid combination."}
            
            totals_map['calories'] = (
                (totals_map['p'] * 4) +
                (totals_map['c'] * 4) +
                (totals_map['f'] * 9)
            )
            
            return {
                "plan": suggestion,
                "totals": totals_map,
                "court": court_name,
                "meal_name": meal_name,
                "explanation": explanation
            }
        
        except Exception as e:
            # Handle rate limiting with retry
            if "429" in str(e) and "RESOURCE_EXHAUSTED" in str(e) and not is_retry:
                logger.warning(f"Rate limit hit for {court_name}/{meal_name}, retrying...")
                delay = random.randint(Config.RETRY_DELAY_MIN, Config.RETRY_DELAY_MAX)
                time.sleep(delay)
                return self._fetch_ai_suggestion_from_api(court_name, meal_name, is_retry=True)
            
            logger.error(f"Gemini API error for {court_name}/{meal_name}: {e}")
            return {"error": "The AI suggestion failed. Try again."}

    def get_ai_suggestion(self, court_name, meal_name):
        """Gets an AI suggestion, checking cache first."""
        # Ensure data is current
        if self._ensure_current_date():
            self._load_all_menu_data()
            # Wait for load
            while not self.data_loaded:
                time.sleep(0.5)
        
        cache_key = (court_name, meal_name)
        
        # Check cache
        if cache_key in self.ai_suggestions_cache:
            return self.ai_suggestions_cache[cache_key]
        
        # Generate on-demand
        logger.info(f"Cache miss for {court_name}/{meal_name}, generating...")
        suggestion = self._fetch_ai_suggestion_from_api(court_name, meal_name)
        
        # Update cache
        with self.cache_lock:
            self.ai_suggestions_cache[cache_key] = suggestion
        
        self._save_ai_cache_to_disk()
        
        return suggestion

    def _background_preloader_task(self):
        """Loads AI cache and generates missing suggestions in parallel."""
        # Wait for menu data
        if not self.data_loaded:
            logger.info("AI Preloader: Waiting for menu data...")
            while not self.data_loaded:
                time.sleep(1)
        
        # Check for stale data
        if self._ensure_current_date():
            self._load_all_menu_data()
            while not self.data_loaded:
                time.sleep(1)
        
        logger.info("AI Preloader: Loading cache...")
        self._load_ai_cache_from_disk()
        
        # Find all unique (court, meal) combinations
        jobs_set = set()
        for item in self.master_item_list:
            jobs_set.add((item['court'], item['meal_name']))
        
        # Find missing jobs
        missing_jobs = []
        with self.cache_lock:
            for job in jobs_set:
                if job not in self.ai_suggestions_cache:
                    missing_jobs.append(job)
        
        if not missing_jobs:
            logger.info(f"AI cache is warm. All {len(jobs_set)} suggestions loaded.")
            return
        
        logger.info(f"AI Preloader: {len(missing_jobs)} missing. Starting generation...")
        
        # Mark as loading
        with self.cache_lock:
            for court, meal in missing_jobs:
                self.ai_suggestions_cache[(court, meal)] = {"status": "loading"}
        
        # Worker function
        def _preload_worker(job):
            court, meal = job
            cache_key = (court, meal)
            try:
                suggestion = self._fetch_ai_suggestion_from_api(court, meal)
                with self.cache_lock:
                    self.ai_suggestions_cache[cache_key] = suggestion
            except Exception as e:
                logger.error(f"Error pre-loading {court}/{meal}: {e}")
                with self.cache_lock:
                    self.ai_suggestions_cache[cache_key] = {
                        "error": "Failed to pre-load suggestion."
                    }
        
        # Generate in parallel with limited workers
        with ThreadPoolExecutor(max_workers=Config.AI_MAX_WORKERS) as executor:
            executor.map(_preload_worker, missing_jobs)
        
        # Save to disk
        self._save_ai_cache_to_disk()
        logger.info(f"AI Preloader: {len(missing_jobs)} new suggestions generated.")

    def start_background_loaders(self):
        """Starts background threads for menu and AI loading."""
        logger.info("Starting background loaders...")
        
        # Thread 1: Load menus
        menu_loader_thread = threading.Thread(
            target=self._load_all_menu_data,
            daemon=True,
            name="MenuLoader"
        )
        menu_loader_thread.start()
        
        # Thread 2: Load AI suggestions
        ai_loader_thread = threading.Thread(
            target=self._background_preloader_task,
            daemon=True,
            name="AIPreloader"
        )
        ai_loader_thread.start()

    # --- MEAL FINDING ALGORITHM ---

    def _run_optimization_for_court(self, available_items, targets, weights, penalties):
        """Runs simulated annealing for a single court's items."""
        if len(available_items) < Config.MIN_ITEMS:
            return None, float('inf'), {}
        
        best_solution, best_score, best_totals = None, float('inf'), {}
        temp = Config.INITIAL_TEMP
        cooling_rate = Config.COOLING_RATE
        iterations = Config.ITERATIONS
        
        # Start with random solution
        initial_size = min(Config.INITIAL_ITEMS, len(available_items))
        current_solution = random.sample(available_items, initial_size)
        
        for _ in range(iterations):
            if temp <= 1:
                break
            
            neighbor = list(current_solution)
            
            # Random action: swap, add, or remove
            action = random.choice(['swap', 'add', 'remove'])
            
            if action == 'swap' and len(neighbor) > 1:
                neighbor[random.randrange(len(neighbor))] = random.choice(available_items)
            
            elif action == 'add' and len(neighbor) < Config.MAX_ITEMS:
                possible_adds = [i for i in available_items if i not in neighbor]
                if possible_adds:
                    neighbor.append(random.choice(possible_adds))
            
            elif action == 'remove' and len(neighbor) > Config.MIN_ITEMS:
                neighbor.pop(random.randrange(len(neighbor)))
            
            # Calculate scores
            current_score, _ = self._calculate_score(current_solution, targets, weights, penalties)
            neighbor_score, neighbor_totals = self._calculate_score(neighbor, targets, weights, penalties)
            
            # Accept or reject
            if neighbor_score < current_score or random.random() < math.exp((current_score - neighbor_score) / temp):
                current_solution = neighbor
            
            # Update best
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
        
        # Ensure data is current
        if self._ensure_current_date():
            self._load_all_menu_data()
        
        if not self.data_loaded:
            return None
        
        # Apply dietary filters
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
        
        # Get available courts
        available_courts = set(
            item['court'] for item in filtered_master_list
            if item['name'] not in exclusion_list and item['meal_name'] in meal_periods_to_check
        )
        
        if not available_courts:
            return None
        
        overall_best_solution, overall_best_score = None, float('inf')
        weights = Config.WEIGHTS
        penalties = Config.PENALTIES
        
        # Check each court
        for court in available_courts:
            court_specific_items = [
                item for item in filtered_master_list
                if item['court'] == court and
                   item['name'] not in exclusion_list and
                   item['meal_name'] in meal_periods_to_check
            ]
            
            # Run optimization
            court_solution, court_score, court_totals = self._run_optimization_for_court(
                court_specific_items, targets, weights, penalties
            )
            
            # Update best
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

import threading
import requests
import json
import re
import random
import math
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai import types


# =============================================================================
# --- BACKEND LOGIC (The Meal Finding Engine) ---
# =============================================================================
class MealFinder:
    """
    Handles all backend operations: fetching data from the Purdue API,
    caching results, and running the algorithm to find optimal meal plans.
    """
    def __init__(self):
        """Initializes the MealFinder, setting up API endpoints and data structures."""
        self.url = "https://api.hfs.purdue.edu/menus/v3/GraphQL"
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
        self.dining_courts = ["Wiley", "Earhart", "Windsor", "Ford", "Hillenbrand"]
        self.todays_date = datetime.now().strftime('%Y-%m-%d')
        self.cache_file = f"menu_cache_{self.todays_date}.json"
        self.master_item_list = []
        self.data_loaded = False

    def _get_numeric_value(self, label_str):
        """Extracts a number from a string label (e.g., '15g' -> 15.0)."""
        if not label_str: return 0.0
        numeric_part = re.search(r'[\d.]+', label_str)
        return float(numeric_part.group(0)) if numeric_part else 0.0

    def _calculate_score(self, meal_plan, targets, weights, penalties):
        """
        Scores a meal plan based on its deviation from target macros.
        A lower score is better. Applies penalties for missing protein or exceeding carbs/fats.
        """
        if not meal_plan: return float('inf'), {}
        totals = {
            'p': sum(item['p'] for item in meal_plan),
            'c': sum(item['c'] for item in meal_plan),
            'f': sum(item['f'] for item in meal_plan)
        }
        errors = {
            'p': totals['p'] - targets['p'],
            'c': totals['c'] - targets['c'],
            'f': totals['f'] - targets['f']
        }
        if errors['p'] < 0: errors['p'] *= penalties['under_p']
        if errors['c'] > 0: errors['c'] *= penalties['over_c']
        if errors['f'] > 0: errors['f'] *= penalties['over_f']
        score = (weights['p'] * (errors['p']**2) + weights['c'] * (errors['c']**2) + weights['f'] * (errors['f']**2))**0.5
        return score, totals

    def _get_menu_data_for_court(self, court, cached_data):
        """Fetches menu data for a single dining court, using cache if available."""
        menu_data = cached_data.get(court)
        if menu_data:
            return court, menu_data, False
        try:
            variables = {"courtName": court, "date": self.todays_date}
            resp = requests.post(self.url, json={"query": self.query, "variables": variables}, headers=self.headers)
            resp.raise_for_status()
            return court, resp.json(), True
        except requests.exceptions.RequestException:
            return court, None, False

    def _load_all_menu_data(self):
        """
        Loads menu data for all dining courts, using multithreading for speed.
        Manages a daily cache to avoid excessive API calls.
        """
        cached_data, needs_to_save_cache = {}, False
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'r') as f:
                try: cached_data = json.load(f).get("data", {})
                except json.JSONDecodeError: pass

        with ThreadPoolExecutor() as executor:
            future_to_court = {executor.submit(self._get_menu_data_for_court, court, cached_data): court for court in self.dining_courts}
            for future in future_to_court:
                court, menu_data, was_fetched = future.result()
                if was_fetched:
                    cached_data[court], needs_to_save_cache = menu_data, True

                if menu_data and 'data' in menu_data and menu_data.get('data', {}).get('diningCourtByName'):
                    if menu_data['data']['diningCourtByName']['dailyMenu']:
                        for meal in menu_data['data']['diningCourtByName']['dailyMenu']['meals']:
                            for station in meal['stations']:
                                for item_appearance in station['items']:
                                    core_item = item_appearance.get('item')
                                    if core_item and core_item.get('nutritionFacts'):
                                        macros = {'Protein': 0, 'Total Carbohydrate': 0, 'Total fat': 0}
                                        
                                        serving_size = ""
                                        for fact in core_item['nutritionFacts']:
                                            if fact['name'] in macros:
                                                macros[fact['name']] = self._get_numeric_value(fact.get('label'))
                                            elif fact['name'] == 'Serving Size':
                                                serving_size = fact.get('label', '')
                                        
                                        if sum(macros.values()) > 0:
                                            traits = [trait['name'] for trait in core_item.get('traits', []) if trait] if core_item.get('traits') else []
                                            self.master_item_list.append({
                                                "name": item_appearance['displayName'],
                                                "p": macros['Protein'], "c": macros['Total Carbohydrate'], "f": macros['Total fat'],
                                                "court": court, "meal_name": meal['name'], "traits": traits,
                                                "serving_size": serving_size
                                            })

        if needs_to_save_cache:
            with open(self.cache_file, 'w') as f: json.dump({"timestamp": datetime.now().isoformat(), "data": cached_data}, f)
        self.data_loaded = True

    def get_top_protein_foods(self, count=25):
        if not self.data_loaded: return []
        unique_foods = { (item['name'], item['p'], item['c'], item['f']): item for item in self.master_item_list }
        protein_dense_foods = []
        for food in unique_foods.values():
            calories = (food['p'] * 4) + (food['c'] * 4) + (food['f'] * 9)
            if calories > 50 and food['p'] > 5:
                protein_per_100kcal = (food['p'] / calories) * 100
                protein_dense_foods.append({**food, "calories": calories, "protein_density": protein_per_100kcal})
        protein_dense_foods.sort(key=lambda x: x['protein_density'], reverse=True)
        return protein_dense_foods[:count]

    def get_ai_suggestion(self, court_name, meal_name):
        """
        Uses the Google Gemini API to suggest a balanced, healthy meal.
        """
        # --- 1. Configure the API ---
        # This securely gets the key you just added to Render.
        API_KEY = os.environ.get("GEMINI_API_KEY")
        if not API_KEY:
            return {"error": "AI service is not configured."}
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')

        # --- 2. Get the menu data (same as before) ---
        if not self.data_loaded:
            self._load_all_menu_data()
            
        available_items = [
            item for item in self.master_item_list 
            if item['court'] == court_name and item['meal_name'] == meal_name
        ]
        
        if not available_items:
            return {"error": "No items found for this dining court and meal."}
        
        # --- 3. Format the data for the AI ---
        # We'll just send a list of item names and their macros
        food_list_str = "\n".join([
            f"- {item['name']} (P:{item['p']}g, C:{item['c']}g, F:{item['f']}g)" 
            for item in available_items
        ])

        # --- 4. Create the prompt ---
        # We're asking the AI to act as a nutritionist and return *only* a JSON list
        prompt = f"""
        You are a Purdue University dining hall nutritionist. 
        Your goal is to help a student pick a balanced, healthy meal.
        
        Here is the full list of available foods:
        {food_list_str}
        
        From that list, please select 3-4 items that make a healthy, balanced meal. 
        Prioritize a lean protein, a vegetable or fruit, and a whole-grain carb.
        
        Return your answer as ONLY a valid JSON list of the exact food names. Do not add any other text.
        Example: ["Grilled Chicken Breast", "Steamed Broccoli", "Brown Rice"]
        """

        # --- 5. Call the API and get the response ---
        try:
            response = model.generate_content(prompt)
            # Clean up the response text (it sometimes adds ```json ... ```)
            clean_response = response.text.strip().replace("```json", "").replace("```", "")
            
            # The AI gives us a list of names
            suggested_names = json.loads(clean_response)

            # --- 6. Find the full items for those names ---
            suggestion = []
            totals_map = {'p': 0, 'c': 0, 'f': 0}
            
            for name in suggested_names:
                # Find the matching item from our original list
                for item in available_items:
                    if item['name'] == name:
                        suggestion.append(item)
                        totals_map['p'] += item['p']
                        totals_map['c'] += item['c']
                        totals_map['f'] += item['f']
                        break
            
            if not suggestion:
                return {"error": "AI could not find a valid combination."}

            # --- 7. Return the final meal plan ---
            totals_map['calories'] = (totals_map['p'] * 4) + (totals_map['c'] * 4) + (totals_map['f'] * 9)
            
            return {"plan": suggestion, "totals": totals_map, "court": court_name, "meal_name": meal_name}

        except Exception as e:
            print(f"Gemini API Error: {e}")
            return {"error": "The AI suggestion failed. Try again."}

    def find_best_meal(self, targets, meal_periods_to_check, exclusion_list=[], dietary_filters={}):
        if not self.data_loaded:
            self._load_all_menu_data()
        
        filtered_master_list = []
        for item in self.master_item_list:
            traits = item.get('traits', [])
            passes_filter = True
            if dietary_filters.get("Vegetarian") and "Vegetarian" not in traits: passes_filter = False
            if dietary_filters.get("Vegan") and "Vegan" not in traits: passes_filter = False
            if dietary_filters.get("No Gluten") and "Contains Gluten" in traits: passes_filter = False
            if dietary_filters.get("No Nuts") and ("Tree Nuts" in traits or "Peanuts" in traits): passes_filter = False
            if dietary_filters.get("No Eggs") and "Eggs" in traits: passes_filter = False
            if passes_filter:
                filtered_master_list.append(item)

        available_items = [item for item in filtered_master_list if item['name'] not in exclusion_list and item['meal_name'] in meal_periods_to_check]
        if len(available_items) < 2: return None

        best_solution, best_score, best_totals = None, float('inf'), {}
        weights = {'p': 3.0, 'c': 1.0, 'f': 1.5}; penalties = {'under_p': 2, 'over_c': 1.2, 'over_f': 3}
        temp, cooling_rate, iterations = 10000, 0.99, 3000
        current_solution = random.sample(available_items, min(4, len(available_items)))
        for _ in range(iterations):
            if temp <= 1: break
            neighbor = list(current_solution)
            if len(neighbor) > 1 and random.random() < 0.7:
                neighbor[random.randrange(len(neighbor))] = random.choice(available_items)
            elif len(neighbor) < 5 and random.random() < 0.5:
                if len(available_items) > len(neighbor): neighbor.append(random.choice([i for i in available_items if i not in neighbor]))
            elif len(neighbor) > 2:
                neighbor.pop(random.randrange(len(neighbor)))
            current_score, _ = self._calculate_score(current_solution, targets, weights, penalties)
            neighbor_score, neighbor_totals = self._calculate_score(neighbor, targets, weights, penalties)
            if neighbor_score < current_score or random.random() < math.exp((current_score - neighbor_score) / temp):
                current_solution = neighbor
            if neighbor_score < best_score:
                best_score, best_totals, best_solution = neighbor_score, neighbor_totals, neighbor
            temp *= cooling_rate
        
        if not best_solution: return None
        return {"score": best_score, "court": best_solution[0]['court'], "meal_name": best_solution[0]['meal_name'], "plan": best_solution, "totals": best_totals}

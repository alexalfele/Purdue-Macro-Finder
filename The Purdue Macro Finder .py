#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import requests
import json
import re
import random
import math
import os
from datetime import datetime

# --- Setup & Helper Functions ---
url = "https://api.hfs.purdue.edu/menus/v3/GraphQL"
headers = {"Content-Type": "application/json"}
query = """
query GetMenu($courtName: String!, $date: Date!) {
  diningCourtByName(name: $courtName) {
    name
    dailyMenu(date: $date) {
      meals { name, stations { name, items { displayName, item { nutritionFacts { name, label } } } } }
    }
  }
}
"""

def get_numeric_value(label_str):
    if not label_str: return 0.0
    numeric_part = re.search(r'[\d.]+', label_str)
    return float(numeric_part.group(0)) if numeric_part else 0.0

def calculate_score(meal_plan, targets, weights, penalties):
    if not meal_plan: return float('inf'), {}
    total_p = sum(item['p'] for item in meal_plan)
    total_c = sum(item['c'] for item in meal_plan)
    total_f = sum(item['f'] for item in meal_plan)
    p_error, c_error, f_error = total_p - targets['p'], total_c - targets['c'], total_f - targets['f']
    if p_error < 0: p_error *= penalties['under_p']
    if c_error > 0: c_error *= penalties['over_c']
    if f_error > 0: f_error *= penalties['over_f']
    score = (weights['p'] * (p_error**2) + weights['c'] * (c_error**2) + weights['f'] * (f_error**2))**0.5
    return score, {"p": total_p, "c": total_c, "f": total_f}

# --- User Input & Configuration ---
print("Purdue Automated Meal Planner  Purdue University")
todays_date = datetime.now().strftime('%Y-%m-%d')
print(f"Today's date is {todays_date}.")

# ** NEW: Meal Selection **
print("\nWhich meal are you planning for?")
print("1. Breakfast")
print("2. Lunch")
print("3. Dinner")
meal_choice = input("Choose a meal (1, 2, or 3): ")

if meal_choice == '1':
    meal_periods_to_check = ["Breakfast"]
elif meal_choice == '2':
    meal_periods_to_check = ["Lunch", "Late Lunch"]
elif meal_choice == '3':
    meal_periods_to_check = ["Dinner"]
else:
    print("Invalid choice. Exiting.")
    exit()

try:
    target_protein = int(input("\nEnter your target PROTEIN (g) for this meal: "))
    target_carbs = int(input("Enter your target CARBS (g) for this meal: "))
    target_fat = int(input("Enter your target FAT (g) for this meal: "))
except ValueError:
    print("Invalid input. Please enter whole numbers.")
    exit()

targets = {'p': target_protein, 'c': target_carbs, 'f': target_fat}
weights = {'p': 3.0, 'c': 1.0, 'f': 1.5}
penalties = {'under_p': 1.8, 'over_c': 1.2, 'over_f': 3}
min_meal_size, max_meal_size = 2, 12

# --- Data Gathering (with Caching) ---
dining_courts = ["Wiley", "Earhart", "Windsor", "Ford", "Hillenbrand"]
cache_file = f"menu_cache_{todays_date}.json"
cached_data = {}
if os.path.exists(cache_file):
    with open(cache_file, 'r') as f:
        cached_data = json.load(f)

master_item_list = []
print("\nGathering all available food items...")
for court in dining_courts:
    menu_data = None
    if court in cached_data:
        menu_data = cached_data[court]
    else:
        try:
            variables = {"courtName": court, "date": todays_date}
            resp = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
            resp.raise_for_status()
            menu_data = resp.json()
            cached_data[court] = menu_data
            with open(cache_file, 'w') as f:
                json.dump(cached_data, f)
        except requests.exceptions.RequestException:
            print(f"  - Could not fetch menu for {court}.")
            continue
    if 'errors' in menu_data or not menu_data.get('data', {}).get('diningCourtByName'):
        continue

    for meal in menu_data['data']['diningCourtByName']['dailyMenu']['meals']:
        for station in meal['stations']:
            for item_appearance in station['items']:
                core_item = item_appearance.get('item')
                if core_item and core_item.get('nutritionFacts'):
                    macros = {'Protein': 0, 'Total Carbohydrate': 0, 'Total fat': 0}
                    for fact in core_item['nutritionFacts']:
                        if fact['name'] in macros:
                            macros[fact['name']] = get_numeric_value(fact.get('label'))
                    if sum(macros.values()) > 0:
                        master_item_list.append({
                            "name": item_appearance['displayName'],
                            "p": macros['Protein'], "c": macros['Total Carbohydrate'], "f": macros['Total fat'],
                            "court": court, "meal_name": meal['name']
                        })

# --- Interactive Recalibration Loop ---
exclusion_list = []
while True:
    # Filter master list by user's chosen meal period and exclusion list
    available_items = [
        item for item in master_item_list 
        if item['name'] not in exclusion_list and item['meal_name'] in meal_periods_to_check
    ]

    if len(available_items) < max_meal_size:
        print("Not enough food items available to generate a new plan.")
        break

    print("\nSearching for the optimal meal plan...")
    # Simulated Annealing Algorithm
    temp, cooling_rate = 10000, 0.99
    initial_size = random.randint(min_meal_size, max_meal_size)
    current_solution = random.sample(available_items, initial_size)
    best_score, best_totals = calculate_score(current_solution, targets, weights, penalties)
    best_solution = current_solution

    while temp > 1:
        neighbor = list(current_solution)
        action = random.choice(['swap', 'add', 'remove'])
        if action == 'swap' and len(neighbor) > 0:
            neighbor[random.randrange(len(neighbor))] = random.choice(available_items)
        elif action == 'add' and len(neighbor) < max_meal_size:
            neighbor.append(random.choice([i for i in available_items if i not in neighbor]))
        elif action == 'remove' and len(neighbor) > min_meal_size:
            neighbor.pop(random.randrange(len(neighbor)))

        current_score, _ = calculate_score(current_solution, targets, weights, penalties)
        neighbor_score, neighbor_totals = calculate_score(neighbor, targets, weights, penalties)

        if neighbor_score < current_score or random.random() < math.exp((current_score - neighbor_score) / temp):
            current_solution = neighbor

        if neighbor_score < best_score:
            best_score, best_totals, best_solution = neighbor_score, neighbor_totals, neighbor

        temp *= cooling_rate

    best_overall_meal = {"score": best_score, "plan": best_solution, "totals": best_totals}

    # --- Display Recommendation & Get Feedback ---
    print("\n" + "="*40)
    print("          🏆 MEAL RECOMMENDATION 🏆")
    print("="*40)

    if not best_overall_meal.get("plan"):
        print("\nCould not find a suitable meal with the remaining items.")
        break

    b = best_overall_meal
    court = b['plan'][0]['court']
    meal_name = b['plan'][0]['meal_name']

    print(f"\nSuggestion: Go to {court.upper()} for {meal_name.upper()}")
    print("\n--- Items to Get ---")
    for i, item in enumerate(b['plan']):
        print(f"  {i+1}. {item['name']} (P:{item['p']:.0f}g, C:{item['c']:.0f}g, F:{item['f']:.0f}g)")

    print("\n--- Meal Totals vs. Your Target ---")
    print(f"   Protein: {b['totals']['p']:.0f}g / {target_protein}g  (Difference: {b['totals']['p'] - target_protein:+.0f}g)")
    print(f"   Carbs:   {b['totals']['c']:.0f}g / {target_carbs}g  (Difference: {b['totals']['c'] - target_carbs:+.0f}g)")
    print(f"   Fat:     {b['totals']['f']:.0f}g / {target_fat}g  (Difference: {b['totals']['f'] - target_fat:+.0f}g)")

    user_choice = input("\nTo remove an item, enter its number (e.g., '2'). Press Enter to accept the meal: ")

    if user_choice == "":
        print("\nEnjoy your meal!")
        break
    else:
        try:
            item_index = int(user_choice) - 1
            if 0 <= item_index < len(b['plan']):
                item_to_remove = b['plan'][item_index]
                exclusion_list.append(item_to_remove['name'])
                print(f"\n--> Excluding '{item_to_remove['name']}'. Recalibrating...")
            else:
                print("Invalid number. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number or press Enter.")


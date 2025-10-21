{\rtf1\ansi\ansicpg1252\cocoartf2822
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 import streamlit as st\
import requests\
import json\
import re\
import random\
import math\
import os\
from datetime import datetime, timedelta\
from concurrent.futures import ThreadPoolExecutor\
import matplotlib.pyplot as plt\
\
# --- BACKEND LOGIC (The MealFinder class is mostly unchanged) ---\
class MealFinder:\
    def __init__(self):\
        self.url = "https://api.hfs.purdue.edu/menus/v3/GraphQL"\
        self.headers = \{"Content-Type": "application/json"\}\
        self.query = """\
        query GetMenu($courtName: String!, $date: Date!) \{\
          diningCourtByName(name: $courtName) \{ name, dailyMenu(date: $date) \{ meals \{ name, stations \{ name, items \{ displayName, item \{ traits \{ name \}, nutritionFacts \{ name, label \} \} \} \} \} \} \}\
        """\
        self.dining_courts = ["Wiley", "Earhart", "Windsor", "Ford", "Hillenbrand"]\
        self.todays_date = datetime.now().strftime('%Y-%m-%d')\
        self.cache_file = f"menu_cache_\{self.todays_date\}.json"\
        self.master_item_list = []\
        self.data_loaded = False\
\
    def _get_numeric_value(self, label_str):\
        if not label_str: return 0.0\
        numeric_part = re.search(r'[\\d.]+', label_str)\
        return float(numeric_part.group(0)) if numeric_part else 0.0\
\
    def _calculate_score(self, meal_plan, targets, weights, penalties):\
        if not meal_plan: return float('inf'), \{\}\
        totals = \{'p': sum(item['p'] for item in meal_plan), 'c': sum(item['c'] for item in meal_plan), 'f': sum(item['f'] for item in meal_plan)\}\
        errors = \{'p': totals['p'] - targets['p'], 'c': totals['c'] - targets['c'], 'f': totals['f'] - targets['f']\}\
        if errors['p'] < 0: errors['p'] *= penalties['under_p']\
        if errors['c'] > 0: errors['c'] *= penalties['over_c']\
        if errors['f'] > 0: errors['f'] *= penalties['over_f']\
        score = (weights['p'] * (errors['p']**2) + weights['c'] * (errors['c']**2) + weights['f'] * (errors['f']**2))**0.5\
        return score, totals\
\
    def _get_menu_data_for_court(self, court, cached_data):\
        menu_data = cached_data.get(court)\
        if menu_data: return court, menu_data, False\
        try:\
            variables = \{"courtName": court, "date": self.todays_date\}\
            resp = requests.post(self.url, json=\{"query": self.query, "variables": variables\}, headers=self.headers)\
            resp.raise_for_status()\
            return court, resp.json(), True\
        except requests.exceptions.RequestException:\
            return court, None, False\
\
    @st.cache_data(ttl=3600) # Streamlit's built-in caching for 1 hour\
    def _load_all_menu_data(_self):\
        master_item_list = []\
        # ... (This is the same data loading logic as before) ...\
        cached_data = \{\} # Simplified for Streamlit's cache\
        with ThreadPoolExecutor() as executor:\
            future_to_court = \{executor.submit(_self._get_menu_data_for_court, court, cached_data): court for court in _self.dining_courts\}\
            for future in future_to_court:\
                court, menu_data, _ = future.result()\
                if menu_data and 'data' in menu_data and menu_data.get('data', \{\}).get('diningCourtByName'):\
                    for meal in menu_data['data']['diningCourtByName']['dailyMenu']['meals']:\
                        for station in meal['stations']:\
                            for item_appearance in station['items']:\
                                core_item = item_appearance.get('item')\
                                if core_item and core_item.get('nutritionFacts'):\
                                    macros = \{'Protein': 0, 'Total Carbohydrate': 0, 'Total fat': 0\}\
                                    for fact in core_item['nutritionFacts']:\
                                        if fact['name'] in macros: macros[fact['name']] = _self._get_numeric_value(fact.get('label'))\
                                    \
                                    traits = [trait['name'] for trait in core_item.get('traits', []) if trait] if core_item.get('traits') else []\
                                    if sum(macros.values()) > 0:\
                                        master_item_list.append(\{"name": item_appearance['displayName'], "p": macros['Protein'], "c": macros['Total Carbohydrate'], "f": macros['Total fat'], "court": court, "meal_name": meal['name'], "traits": traits\})\
        return master_item_list\
\
    def find_best_meal(self, targets, meal_periods_to_check, exclusion_list=[], dietary_filters=\{\}):\
        if not self.data_loaded:\
            self.master_item_list = self._load_all_menu_data()\
            self.data_loaded = True\
        \
        # ... (The rest of the find_best_meal logic is the same) ...\
        filtered_master_list = []\
        for item in self.master_item_list:\
            traits = item.get('traits', [])\
            passes_filter = True\
            if dietary_filters.get("Vegetarian") and "Vegetarian" not in traits: passes_filter = False\
            if dietary_filters.get("Vegan") and "Vegan" not in traits: passes_filter = False\
            if dietary_filters.get("No Gluten") and "Gluten" in traits: passes_filter = False\
            if dietary_filters.get("No Nuts") and ("Tree Nuts" in traits or "Peanuts" in traits): passes_filter = False\
            if dietary_filters.get("No Eggs") and "Eggs" in traits: passes_filter = False\
            if passes_filter: filtered_master_list.append(item)\
        \
        available_items = [item for item in filtered_master_list if item['name'] not in exclusion_list and item['meal_name'] in meal_periods_to_check]\
        if len(available_items) < 2: return None\
\
        best_solution, best_score, best_totals = None, float('inf'), \{\}\
        weights = \{'p': 3.0, 'c': 1.0, 'f': 1.5\}\
        penalties = \{'under_p': 1.5, 'over_c': 1.2, 'over_f': 1.5\}\
        temp, cooling_rate, iterations = 10000, 0.99, 3000\
        current_solution = random.sample(available_items, min(4, len(available_items)))\
        for _ in range(iterations):\
            if temp <= 1: break\
            neighbor = list(current_solution)\
            if len(neighbor) > 1 and random.random() < 0.7: neighbor[random.randrange(len(neighbor))] = random.choice(available_items)\
            elif len(neighbor) < 5 and random.random() < 0.5:\
                if len(available_items) > len(neighbor): neighbor.append(random.choice([i for i in available_items if i not in neighbor]))\
            elif len(neighbor) > 2: neighbor.pop(random.randrange(len(neighbor)))\
            current_score, _ = self._calculate_score(current_solution, targets, weights, penalties)\
            neighbor_score, neighbor_totals = self._calculate_score(neighbor, targets, weights, penalties)\
            if neighbor_score < current_score or random.random() < math.exp((current_score - neighbor_score) / temp): current_solution = neighbor\
            if neighbor_score < best_score: best_score, best_totals, best_solution = neighbor_score, neighbor_totals, neighbor\
            temp *= cooling_rate\
        \
        if not best_solution: return None\
        return \{"score": best_score, "court": best_solution[0]['court'], "meal_name": best_solution[0]['meal_name'], "plan": best_solution, "totals": best_totals\}\
\
# --- STREAMLIT UI (The Frontend) ---\
st.set_page_config(layout="wide", page_title="Purdue Meal Planner")\
\
# Initialize MealFinder and session state\
if 'meal_finder' not in st.session_state:\
    st.session_state.meal_finder = MealFinder()\
if 'exclusion_list' not in st.session_state:\
    st.session_state.exclusion_list = []\
if 'result' not in st.session_state:\
    st.session_state.result = None\
\
# Sidebar for inputs\
with st.sidebar:\
    st.title(" Purdue Meal Planner")\
    \
    meal_choice = st.selectbox("Meal Period:", ["Breakfast", "Lunch", "Dinner"])\
    \
    st.subheader("Target Macros (g)")\
    target_protein = st.number_input("Protein", min_value=0, value=50)\
    target_carbs = st.number_input("Carbohydrates", min_value=0, value=50)\
    target_fat = st.number_input("Fat", min_value=0, value=20)\
    \
    st.subheader("Dietary Filters")\
    filters = \{\
        "Vegetarian": st.checkbox("Vegetarian"),\
        "Vegan": st.checkbox("Vegan"),\
        "No Gluten": st.checkbox("Gluten-Free"),\
        "No Nuts": st.checkbox("Nut-Free"),\
        "No Eggs": st.checkbox("Egg-Free")\
    \}\
\
    if st.button("Find Meal", use_container_width=True, type="primary"):\
        with st.spinner("Analyzing menus across campus..."):\
            targets = \{'p': target_protein, 'c': target_carbs, 'f': target_fat\}\
            meal_periods = ["Lunch", "Late Lunch"] if meal_choice == "Lunch" else [meal_choice]\
            active_filters = \{key: val for key, val in filters.items() if val\}\
            \
            st.session_state.result = st.session_state.meal_finder.find_best_meal(\
                targets, meal_periods, st.session_state.exclusion_list, active_filters\
            )\
\
    if st.button("Reset Exclusions", use_container_width=True):\
        st.session_state.exclusion_list = []\
        st.success("Exclusion list cleared!")\
\
# Main content area for results\
st.title("\uc0\u55356 \u57286  Meal Recommendation")\
\
if not st.session_state.result:\
    st.info("Enter your macros in the sidebar and click 'Find Meal' to get started!")\
else:\
    result = st.session_state.result\
    \
    st.header(f"Best match is for \{result['meal_name']\} at \{result['court']\}")\
    \
    st.subheader("Items to Get")\
    for i, item in enumerate(result['plan']):\
        col1, col2 = st.columns([4, 1])\
        with col1:\
            st.text(f"\'95 \{item['name']\} (P:\{item['p']:.0f\}g, C:\{item['c']:.0f\}g, F:\{item['f']:.0f\}g)")\
        with col2:\
            if st.button(f"Remove##\{i\}", key=f"remove_\{i\}"):\
                st.session_state.exclusion_list.append(item['name'])\
                st.info(f"'\{item['name']\}' excluded. Click 'Find Meal' again to recalibrate.")\
    \
    st.subheader("Meal Totals vs. Your Target")\
    totals = result['totals']\
    p_diff, c_diff, f_diff = totals['p'] - target_protein, totals['c'] - target_carbs, totals['f'] - target_fat\
    st.metric(label="Protein", value=f"\{totals['p']:.0f\}g", delta=f"\{p_diff:+.0f\}g")\
    st.metric(label="Carbohydrates", value=f"\{totals['c']:.0f\}g", delta=f"\{c_diff:+.0f\}g")\
    st.metric(label="Fat", value=f"\{totals['f']:.0f\}g", delta=f"\{f_diff:+.0f\}g")\
    st.caption(f"Match Score: \{result['score']:.1f\} (lower is better)")\
    \
    # Visualization\
    st.subheader("Macro Breakdown by Calories")\
    p, c, f = totals['p'], totals['c'], totals['f']\
    if p + c + f > 0:\
        calories = [p * 4, c * 4, f * 9]\
        labels = [f'Protein\\n\{p:.0f\}g', f'Carbs\\n\{c:.0f\}g', f'Fat\\n\{f:.0f\}g']\
        colors = ['#3498db', '#2ecc71', '#e74c3c']\
        \
        fig, ax = plt.subplots()\
        ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90, colors=colors)\
        ax.axis('equal')\
        st.pyplot(fig)\
}
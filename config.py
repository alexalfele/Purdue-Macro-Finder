"""
Configuration constants for Purdue Macro Finder
"""

class Config:
    """Application configuration and constants"""
    
    # Optimization parameters
    WEIGHTS = {'p': 3.0, 'c': 1.0, 'f': 1.5}
    PENALTIES = {'under_p': 2, 'over_c': 1.2, 'over_f': 3}
    INITIAL_TEMP = 10000
    COOLING_RATE = 0.99
    ITERATIONS = 3000
    
    # Meal plan constraints
    MIN_ITEMS = 2
    MAX_ITEMS = 5
    INITIAL_ITEMS = 4
    
    # API settings
    API_TIMEOUT = 10
    AI_MAX_WORKERS = 4
    RETRY_DELAY_MIN = 5
    RETRY_DELAY_MAX = 15
    
    # Cache settings
    CACHE_PREFIX_MENU = "menu_cache_"
    CACHE_PREFIX_AI = "ai_cache_"
    
    # Validation limits
    MAX_MACRO_TARGET = 500  # grams
    MIN_MACRO_TARGET = 0
    
    # Dining courts
    DINING_COURTS = ["Wiley", "Earhart", "Windsor", "Ford", "Hillenbrand"]
    
    # Meal periods
    MEAL_PERIODS = ["Breakfast", "Lunch", "Dinner", "Late Night"]
    
    # Purdue API
    PURDUE_API_URL = "https://api.hfs.purdue.edu/menus/v3/GraphQL"
    
    # AI Model
    GEMINI_MODEL = "gemini-2.0-flash-exp"
    
    # Rate limiting
    RATE_LIMIT_PER_DAY = 200
    RATE_LIMIT_PER_HOUR = 50
    RATE_LIMIT_PER_MINUTE = 20

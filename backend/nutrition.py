"""Nutrition guidelines reference module for BR100 training.

Provides per-workout-type fueling guidance (pre/during/post) based on
estimated duration tiers: short (<60 min), medium (60-90 min), long (>90 min).
"""

TIERS = {
    "short": {
        "label": "Short (<60 min)",
        "pre_run": {
            "carbs_g": "20-30",
            "timing": "Optional — light snack 30-60 min before if hungry",
            "examples": ["banana", "toast with jam", "handful of pretzels"],
        },
        "during_run": {
            "water_oz_per_hr": "12-16",
            "carbs_g_per_hr": "0",
            "sodium_mg_per_hr": "0",
            "notes": "Water only. No fuel needed for runs under 60 min.",
        },
        "post_run": {
            "protein_g": "15-20",
            "carbs_g": "30-40",
            "recovery_window": "Within 60 min if hungry",
            "examples": ["Greek yogurt + fruit", "chocolate milk", "protein bar"],
        },
    },
    "medium": {
        "label": "Medium (60-90 min)",
        "pre_run": {
            "carbs_g": "40-60",
            "timing": "1.5-2 hr before",
            "examples": ["oatmeal + banana", "toast + PB", "bagel with honey"],
        },
        "during_run": {
            "water_oz_per_hr": "16-20",
            "carbs_g_per_hr": "30-60",
            "sodium_mg_per_hr": "200-400",
            "notes": "1 gel or equivalent (~100 cal) around 45 min. Electrolyte tabs helpful.",
        },
        "post_run": {
            "protein_g": "20-30",
            "carbs_g": "40-60",
            "recovery_window": "Within 30-60 min",
            "examples": ["protein shake + banana", "eggs + toast", "PB toast + milk"],
        },
    },
    "long": {
        "label": "Long (>90 min)",
        "pre_run": {
            "carbs_g": "60-100",
            "timing": "2-3 hr before",
            "examples": ["oatmeal + banana + honey", "bagel + PB + banana", "rice bowl + eggs"],
        },
        "during_run": {
            "water_oz_per_hr": "20-24",
            "carbs_g_per_hr": "200-250 cal/hr (50-60g carbs)",
            "sodium_mg_per_hr": "400-800",
            "notes": "Fuel every 30-45 min. Mix gels, chews, real food. Practice race nutrition!",
        },
        "post_run": {
            "protein_g": "30-40",
            "carbs_g": "60-80",
            "recovery_window": "Within 30 min — critical for recovery",
            "examples": ["protein shake + PB toast", "chicken + rice", "recovery smoothie"],
        },
    },
    "race_100mi": {
        "label": "Race Day (100 miles)",
        "pre_run": {
            "carbs_g": "100-150",
            "timing": "3 hr before start",
            "examples": ["rice + eggs + toast", "oatmeal + banana + PB + honey", "bagels + cream cheese"],
        },
        "during_run": {
            "water_oz_per_hr": "20-28",
            "carbs_g_per_hr": "250-300 cal/hr (60-80g carbs)",
            "sodium_mg_per_hr": "500-1000",
            "notes": "Mix gels with real food (PB&J, boiled potatoes, broth). Eat before hungry. Walk while eating. Shift to savory after 12+ hrs. Use aid stations strategically.",
        },
        "post_run": {
            "protein_g": "40-50",
            "carbs_g": "80-100",
            "recovery_window": "Eat whatever sounds good. Prioritize fluids + sodium.",
            "examples": ["pizza", "burger", "soup + bread", "whatever your body craves"],
        },
    },
}


def get_nutrition_tier(distance_miles: float, duration_minutes: float | None = None) -> str:
    """Determine nutrition tier based on distance and/or duration.

    Returns one of: 'short', 'medium', 'long'.
    """
    if duration_minutes is not None:
        if duration_minutes < 60:
            return "short"
        elif duration_minutes <= 90:
            return "medium"
        else:
            return "long"

    # Estimate from distance if no duration given (assume ~10 min/mile easy pace)
    estimated_duration = distance_miles * 10
    if estimated_duration < 60:
        return "short"
    elif estimated_duration <= 90:
        return "medium"
    else:
        return "long"


def get_guidelines_for_workout(workout_type: str, distance_miles: float,
                                duration_minutes: float | None = None) -> dict:
    """Get full nutrition guidelines for a given workout.

    Returns dict with tier, pre_run, during_run, post_run guidelines.
    """
    if workout_type == "race" and distance_miles >= 50:
        tier_key = "race_100mi"
    else:
        tier_key = get_nutrition_tier(distance_miles, duration_minutes)

    tier_data = TIERS[tier_key]
    return {
        "tier": tier_key,
        "tier_label": tier_data["label"],
        "pre_run": tier_data["pre_run"],
        "during_run": tier_data["during_run"],
        "post_run": tier_data["post_run"],
        "distance_miles": distance_miles,
        "duration_minutes": duration_minutes,
        "workout_type": workout_type,
    }

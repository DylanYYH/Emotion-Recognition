import datetime

def get_historical_context():
    """
    Returns likely needs based on time of day.
    """
    now = datetime.datetime.now()
    hour = now.hour
    
    # Simple heuristic: 
    # Night (0-6): Sleepy, Hungry
    # Morning (6-11): Hungry, Playful
    # Afternoon (12-17): Tired, Hungry
    # Evening (18-23): Sleepy
    
    if 0 <= hour < 6:
        return ["Sleepy", "Hungry"]
    elif 6 <= hour < 12:
        return ["Hungry", "Playful"]
    elif 12 <= hour < 18:
        return ["Tired", "Hungry"]
    else:
        return ["Sleepy"]

def get_suggestions(need):
    """
    Returns steps to soothe based on need.
    """
    suggestions_map = {
        "Hungry": ["Check when the baby was last fed.", "Offer breast or bottle.", "Burp the baby."],
        "Sleepy": ["Check for signs of tiredness (rubbing eyes).", "Swaddle the baby.", "Rock gently in a quiet room."],
        "Pain": ["Check for fever or illness.", "Check diaper.", "Check for tight clothing.", "If symptoms persist, consult a doctor."],
        "Tired": ["Reduce stimulation.", "Create a calm environment.", "Try white noise."],
        "Discomfort": ["Check diaper.", "Check room temperature.", "Check for skin irritation."],
        "Sad": ["Cuddle and comfort.", "Talk soothingly.", "Offer a favorite toy."],
        "Distressed": ["Check immediate needs (hunger, diaper).", "Hold close and rock.", "Sing a lullaby."],
        "Neutral": ["Baby seems okay.", "Engage in play if awake.", "Monitor for changes."]
    }
    
    return suggestions_map.get(need, ["Comfort the baby.", "Check basic needs."])

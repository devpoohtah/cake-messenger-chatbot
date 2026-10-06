from app.services.ai_service import GeminiAIService

ai = GeminiAIService()
products = ["Nutella Ferrero Cake", "Chocolate Moist Cake", "Leche Flan", "Mango Float", "Yema Cake"]

tests = [
    ("how much is the leche flan?", "idle"),
    ("i want 2 mango float", "idle"),
    ("Ana Cruz, 09171234567, Rizal St", "collecting_details"),
    ("yes confirm", "awaiting_confirmation"),
    ("yes", "idle"),
]
for msg, state in tests:
    print(f"[{state}] {msg!r}\n   -> {ai.interpret_message(msg, state, products)}\n")
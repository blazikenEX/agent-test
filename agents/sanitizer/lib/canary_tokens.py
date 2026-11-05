import os
import secrets
import json
from datetime import datetime

# Define target paths for quarantine (aligned with regex_filters.py)
QUARANTINE_DIR = os.path.join(os.path.dirname(__file__), "../quarantine")

class CanaryTokenManager:
    def __init__(self):
        # A simple in-memory session store for tokens. 
        # In a multi-agent system, this could be written to a temporary session file.
        self.active_tokens = set()

    def generate_token(self, prefix: str = "SECRET_CANARY") -> str:
        """Generates a unique, high-entropy cryptographic token to inject."""
        # Creates a random token like 'SECRET_CANARY_a1b2c3d4e5'
        random_hex = secrets.token_hex(5)
        token = f"{prefix}_{random_hex}"
        self.active_tokens.add(token)
        return token

    def check_leak(self, text: str) -> bool:
        """Checks if any of the active canary tokens are contained within the text."""
        for token in self.active_tokens:
            if token in text:
                return True
        return False

    def quarantine_payload(self, text: str, reason: str):
        """Saves a leaked or suspicious text input to the shared quarantine folder."""
        if not os.path.exists(QUARANTINE_DIR):
            os.makedirs(QUARANTINE_DIR)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"flagged_canary_{timestamp}.json"
        filepath = os.path.join(QUARANTINE_DIR, filename)
        
        data = {
            "timestamp": timestamp,
            "reason": reason,
            "raw_input": text,
            "matched_tokens": [t for t in self.active_tokens if t in text]
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        print(f"⚠️ SECURITY: Canary leak detected! Payload quarantined to {filepath}")

    def inspect_output(self, text: str) -> dict:
        """
        Inspects downstream or upstream outputs to ensure tokens haven't leaked out.
        If a token is found, it flags and quarantines the entry.
        """
        if self.check_leak(text):
            self.quarantine_payload(text, "Canary Token Leak Detected")
            return {
                "status": "FLAGGED",
                "leak_detected": True,
                "text": text
            }
        
        return {
            "status": "CLEAN",
            "leak_detected": False,
            "text": text
        }

# --- Demonstration & Verification ---
if __name__ == "__main__":
    manager = CanaryTokenManager()
    
    # 1. Generate a token to plant inside system logs or system context
    token_1 = manager.generate_token()
    print(f"Generated Canary Token: {token_1}")
    
    # System processes data...
    
    # 2. Simulate a clean output that does not leak the token
    clean_output = "The user data has been processed correctly and anonymized successfully."
    result_clean = manager.inspect_output(clean_output)
    print(f"\n--- Clean Test ---\nStatus: {result_clean['status']}")
    
    # 3. Simulate an attack or leakage (e.g., LLM prints out system secrets)
    leaked_output = f"System Error: Cannot complete execution. Access code: {token_1}."
    result_leak = manager.inspect_output(leaked_output)
    print(f"\n--- Leak Test ---\nStatus: {result_leak['status']}")

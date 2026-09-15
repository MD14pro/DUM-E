import json
import requests
from robot_controller import YouBot

SYSTEM_PROMPT = """
You are the high-level autonomous task planner for a mobile robot (youBot) in CoppeliaSim.

Available Inventory in Cupboard_1:
- "SS"  (Signals and Systems)
- "NT"  (Network Theory)
- "EDC" (Electronic Devices and Circuits)
- "ADC" (Analog and Digital Communication)
- "AC"  (Analog Circuits)

Destination Shelf:
- "Cupboard_2"

Available Robot Actions:
- {"action": "navigate_to", "target": "<book_name or Cupboard_2>"}
- {"action": "pick_book", "target": "<book_name>"}
- {"action": "drop_book"}

RULES:
1. Always map the requested book to its precise ID: "SS", "NT", "EDC", "ADC", or "AC".
2. When asked to pickup a book and deliver to Cupboard_2, output this EXACT 4-step sequence:
   [
     {"action": "navigate_to", "target": "<book_name>"},
     {"action": "pick_book", "target": "<book_name>"},
     {"action": "navigate_to", "target": "Cupboard_2"},
     {"action": "drop_book"}
   ]

Return strictly valid JSON:
{
  "plan": [
    {"action": "action_name", "target": string or null}
  ]
}
No explanations, raw JSON only.
"""

def get_llm_plan(user_instruction: str):
    print(f"\n[Planner] Planning: '{user_instruction}'...")
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": f"{SYSTEM_PROMPT}\nUser Request: \"{user_instruction}\"\nOutput:",
                "stream": False,
                "format": "json"
            },
            timeout=30
        )
        response_data = response.json()
        raw_text = response_data["response"].strip()
        parsed = json.loads(raw_text)

        if isinstance(parsed, dict):
            if "plan" in parsed and isinstance(parsed["plan"], list):
                return parsed["plan"]
            for val in parsed.values():
                if isinstance(val, list):
                    return val
            if "action" in parsed:
                return [parsed]
        elif isinstance(parsed, list):
            return parsed

        return []

    except Exception as e:
        print(f"[Error] Plan generation failed: {e}")
        return []

def execute_plan(bot: YouBot, plan: list):
    print("\n--- Starting Execution ---")
    for idx, step in enumerate(plan, start=1):
        act = step.get("action")
        target = step.get("target")

        print(f"Step {idx}: '{act}' (target: {target})")

        if act == "navigate_to":
            bot.navigate_to(target)
        elif act == "pick_book":
            bot.pick_action(book_name=target or "EDC")
        elif act == "drop_book":
            bot.drop_action()
        else:
            print(f"Unknown action: {act}")

    print("--- Mission Completed ---\n")
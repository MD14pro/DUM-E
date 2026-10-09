"""
Task planner.

Purane version ke do problems the:
  1. Prompt me "SS" hardcoded thi jo scene me exist hi nahi karti - LLM
     confidently uska plan bana deta tha aur bot crash hota tha.
  2. Ollama band ho to poora system ruk jaata tha.

Ab: catalog live backend se aata hai, plan ko execute karne se PEHLE validate
kiya jaata hai, aur Ollama na mile to deterministic parser chal jaata hai.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Optional

import requests

import config as C

VALID_ACTIONS = {'navigate_to', 'pick_book', 'place_on_table', 'shelve_book', 'go_home'}

PROMPT = """You are the task planner for a library robot (KUKA youBot) in CoppeliaSim.

Books currently available on Cupboard_1:
{catalog}

Locations: "Cupboard_1" (source shelf), "Drop_Table" (reader's desk),
"Cupboard_2" (return shelf).

Allowed actions, nothing else:
  {{"action":"navigate_to","target":"<book code or location>"}}
  {{"action":"pick_book","target":"<book code>"}}
  {{"action":"place_on_table","target":null}}
  {{"action":"shelve_book","target":"<book code>"}}
  {{"action":"go_home","target":null}}

To deliver a book to the reader, emit exactly:
  navigate_to <code> -> pick_book <code> -> navigate_to Drop_Table -> place_on_table

To return a book, emit exactly:
  navigate_to Drop_Table -> shelve_book <code>

Only use book codes from the list above. If the request names a book that is
not in the list, return {{"plan": [], "error": "not in catalog"}}.

Reply with raw JSON only: {{"plan":[...]}}
"""


# ---------------------------------------------------------------- catalog
def fetch_catalog() -> dict:
    """Backend se live availability. Backend down ho to config fallback."""
    try:
        r = requests.get(f'{C.BACKEND_URL}/api/books', timeout=3)
        r.raise_for_status()
        return {b['code']: b for b in r.json()}
    except Exception:
        return {k: {'code': k, 'title': v['title'], 'status': 'available'}
                for k, v in C.CATALOG.items()}


def catalog_lines(cat: dict) -> str:
    return '\n'.join(
        f'  - "{c}" = {b["title"]} [{b.get("status", "available")}]'
        for c, b in sorted(cat.items()))


def resolve_code(text: str, cat: dict) -> Optional[str]:
    """'analog circuits', 'a.c.', 'netwrk theory' -> code. Fuzzy but strict."""
    t = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    words = t.split()

    for code in cat:
        if code.lower() in words:
            return code
    # ADC/AC overlap se bachne ke liye pehle lamba title match karo
    for code, b in sorted(cat.items(), key=lambda kv: -len(kv[1]['title'])):
        title = b['title'].lower()
        if title in t:
            return code
        key = [w for w in re.split(r'[^a-z]+', title) if len(w) > 3]
        if key and all(w in t for w in key):
            return code
    titles = {b['title'].lower(): c for c, b in cat.items()}
    close = difflib.get_close_matches(t, list(titles), n=1, cutoff=0.55)
    if close:
        return titles[close[0]]
    return None


# ------------------------------------------------------------- fallback
def rule_based_plan(instruction: str, cat: dict) -> list:
    """Ollama ke bina bhi station chalta rahe."""
    low = instruction.lower()
    code = resolve_code(instruction, cat)
    if not code:
        return []
    if any(w in low for w in ('return', 'wapas', 'shelve', 'put back', 'send back')):
        return [{'action': 'navigate_to', 'target': 'Drop_Table'},
                {'action': 'shelve_book', 'target': code}]
    return [{'action': 'navigate_to', 'target': code},
            {'action': 'pick_book', 'target': code},
            {'action': 'navigate_to', 'target': 'Drop_Table'},
            {'action': 'place_on_table', 'target': None}]


# ------------------------------------------------------------- validation
def validate(plan: list, cat: dict) -> tuple[list, list]:
    """Sirf saaf plan hi robot tak jaaye. (clean_plan, problems) return."""
    clean, problems = [], []
    for step in plan:
        if not isinstance(step, dict):
            problems.append(f'step dict nahi hai: {step!r}')
            continue
        act = str(step.get('action', '')).strip()
        tgt = step.get('target')
        if act not in VALID_ACTIONS:
            problems.append(f"unknown action '{act}'")
            continue
        if act in ('pick_book', 'shelve_book'):
            code = (tgt or '').upper()
            if code not in cat:
                guess = resolve_code(str(tgt), cat)
                if not guess:
                    problems.append(f"'{tgt}' catalog me nahi hai")
                    continue
                code = guess
            tgt = code
        if act == 'navigate_to' and tgt:
            t = str(tgt)
            if t.upper() in cat:
                tgt = t.upper()
            elif t not in ('Drop_Table', 'Drop_Spot', 'Cupboard_1', 'Cupboard_2'):
                guess = resolve_code(t, cat)
                if guess:
                    tgt = guess
                else:
                    problems.append(f"unknown nav target '{t}'")
                    continue
        clean.append({'action': act, 'target': tgt})

    # pick ke baad place na ho to add kar do
    if any(s['action'] == 'pick_book' for s in clean) and \
       not any(s['action'] in ('place_on_table', 'shelve_book') for s in clean):
        clean += [{'action': 'navigate_to', 'target': 'Drop_Table'},
                  {'action': 'place_on_table', 'target': None}]
        problems.append('plan adhoora tha - place_on_table add kiya')
    return clean, problems


# --------------------------------------------------------------- planner
def get_llm_plan(instruction: str, cat: Optional[dict] = None) -> list:
    cat = cat or fetch_catalog()
    print(f"\n[planner] '{instruction}'")

    raw_plan = []
    try:
        r = requests.post(C.OLLAMA_URL, timeout=C.OLLAMA_TIMEOUT, json={
            'model': C.OLLAMA_MODEL,
            'prompt': PROMPT.format(catalog=catalog_lines(cat))
                      + f'\nUser request: "{instruction}"\nOutput:',
            'stream': False,
            'format': 'json',
            'options': {'temperature': 0.1},
        })
        r.raise_for_status()
        parsed = json.loads(r.json()['response'].strip())
        if isinstance(parsed, dict):
            raw_plan = parsed.get('plan') or next(
                (v for v in parsed.values() if isinstance(v, list)), [])
            if not raw_plan and 'action' in parsed:
                raw_plan = [parsed]
        elif isinstance(parsed, list):
            raw_plan = parsed
    except Exception as e:
        print(f"[planner] LLM unavailable ({type(e).__name__}) - rule parser use kar raha hoon.")

    plan, problems = validate(raw_plan, cat)
    for p in problems:
        print(f"[planner] fix: {p}")

    if not plan:
        plan = rule_based_plan(instruction, cat)
        if plan:
            print('[planner] rule-based plan banaya.')

    if not plan:
        print('[planner] Ye request samajh nahi aayi. Available: '
              + ', '.join(sorted(cat)))
    return plan


# -------------------------------------------------------------- execution
def execute_plan(bot, plan: list) -> bool:
    print('\n--- execution start ---')
    ok = True
    for i, step in enumerate(plan, 1):
        act, tgt = step['action'], step.get('target')
        print(f'  {i}/{len(plan)}  {act}' + (f'  -> {tgt}' if tgt else ''))

        if act == 'navigate_to':
            if tgt in ('Drop_Table', 'Drop_Spot'):
                ok = bot.go_to_table()
            elif tgt == 'Cupboard_2':
                ok = bot.go_to_return_shelf()
            else:
                ok = bot.go_to_book(tgt)
        elif act == 'pick_book':
            ok = bot.pick_book(tgt)
        elif act == 'place_on_table':
            ok = bot.place_on_table()
        elif act == 'shelve_book':
            ok = bot.shelve_book(tgt)
        elif act == 'go_home':
            bot.go_home()

        if not ok:
            print(f'--- step {i} fail hua, mission abort ---\n')
            if bot.is_grasped:
                bot.place_on_table()
            return False

    print('--- mission complete ---\n')
    return True

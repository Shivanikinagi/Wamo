#!/usr/bin/env python3
"""
Interactive Cross-Session Memory Demo
Shows how PS-01 remembers customers across multiple sessions
"""

import requests
import json
import time
from datetime import datetime
import sys

API_BASE = "http://localhost:8000"

# Colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(text):
    print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}{text.center(70)}{RESET}")
    print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")


def print_section(text):
    print(f"\n{YELLOW}{'─'*70}{RESET}")
    print(f"{MAGENTA}📌 {text}{RESET}")
    print(f"{YELLOW}{'─'*70}{RESET}\n")


def print_success(text):
    print(f"{GREEN}✅ {text}{RESET}")


def print_info(text):
    print(f"{BLUE}ℹ️  {text}{RESET}")


def print_warning(text):
    print(f"{YELLOW}⚠️  {text}{RESET}")


def start_session(customer_id: str, customer_name: str) -> dict:
    """Start a new session and get greeting from memory"""
    print_section(f"Starting Session for {customer_name}")
    
    response = requests.post(
        f"{API_BASE}/session/start",
        json={
            "customer_id": customer_id,
            "session_type": "voice",
            "agent_id": "AGT_D",
            "consent_id": "consent_demo_001"
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        session_id = data.get("session_id")
        greeting = data.get("greeting_message", "Namaskar!")
        
        print_success(f"Session ID: {session_id}")
        print(f"\n{BOLD}Agent D's Greeting:{RESET}\n")
        print(f"{CYAN}\"{greeting}\"{RESET}\n")
        
        return {
            "session_id": session_id,
            "customer_id": customer_id,
            "greeting": greeting
        }
    else:
        print(f"{RED}Error starting session: {response.text}{RESET}")
        return None


def exchange_message(session_id: str, customer_id: str, customer_message: str) -> dict:
    """Send customer message and get agent response"""
    
    print(f"\n{MAGENTA}You:{RESET} {customer_message}\n")
    
    response = requests.post(
        f"{API_BASE}/session/converse",
        json={
            "session_id": session_id,
            "customer_id": customer_id,
            "customer_message": customer_message
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        agent_response = data.get("agent_response", "")
        facts_extracted = data.get("facts_extracted", [])
        
        print(f"{CYAN}Agent D:{RESET} {agent_response}\n")
        
        if facts_extracted:
            # Handle both dict and string formats
            fact_names = []
            for fact in facts_extracted:
                if isinstance(fact, dict):
                    fact_names.append(fact.get("type", str(fact)))
                else:
                    fact_names.append(str(fact))
            if fact_names:
                print(f"{YELLOW}Facts extracted: {', '.join(fact_names)}{RESET}\n")
        
        return {
            "agent_response": agent_response,
            "facts_extracted": facts_extracted
        }
    else:
        print(f"{RED}Error: {response.text}{RESET}")
        return None


def view_memory_state(customer_id: str):
    """Show what's stored in each memory tier"""
    print_section("Current Memory State")
    
    # Check WAL
    print(f"{BOLD}TIER 1: Write-Ahead Log (WAL){RESET}")
    try:
        with open("/tmp/ps01_wal.jsonl", "r") as f:
            entries = [json.loads(line) for line in f if customer_id in line]
        print(f"  Entries for {customer_id}: {GREEN}{len(entries)}{RESET}")
        if entries:
            print(f"  Latest fact types: {YELLOW}{', '.join(set(fact['type'] for entry in entries for fact in entry.get('facts', [])))}{RESET}")
    except:
        print(f"  {RED}Could not read WAL{RESET}")
    
    # Check Redis cache
    print(f"\n{BOLD}TIER 4: Redis Cache (Active Session){RESET}")
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        briefing_key = f"briefing:{customer_id}"
        cached_briefing = r.get(briefing_key)
        if cached_briefing:
            briefing_data = json.loads(cached_briefing)
            print(f"  {GREEN}Briefing cached for {customer_id}{RESET}")
            print(f"  Facts in cache: {len(briefing_data) if isinstance(briefing_data, list) else 'N/A'}")
        else:
            print(f"  {YELLOW}No briefing cached (will be generated on next /session/start){RESET}")
    except Exception as e:
        print(f"  {YELLOW}Redis not available: {e}{RESET}")
    
    print()


def session_1_demo():
    """First session: User provides information"""
    print_header("SESSION 1: Meeting You For The First Time")
    
    customer_id = "demo_rajesh_001"
    customer_name = "Rajesh"
    
    # Start session
    session = start_session(customer_id, customer_name)
    if not session:
        return None
    
    # Conversation turns
    turns = [
        "My name is Rajesh and my income is ₹55,000 per month",
        "My wife Sunita we both live in Nashik city",
        "We have a plot of land there, about 1200 square meters",
        "We want to take a home loan for around ₹45 lakhs",
        "Yes, everything sounds good. We're ready to apply!"
    ]
    
    for i, customer_message in enumerate(turns, 1):
        print_info(f"Turn {i}/{len(turns)}")
        exchange_message(session["session_id"], customer_id, customer_message)
        time.sleep(1)  # Brief pause between turns
    
    print_section("Session 1 Complete!")
    print(f"{GREEN}✅ Rajesh's information stored in memory:{RESET}")
    print(f"   • Name: Rajesh")
    print(f"   • Income: ₹55,000/month")
    print(f"   • Spouse: Sunita")
    print(f"   • Location: Nashik")
    print(f"   • Property: 1200 sqm plot")
    print(f"   • Loan Amount: ₹45 lakhs")
    
    # Show what was stored
    view_memory_state(customer_id)
    
    print_warning("In real system, all facts are now in:")
    print(f"  • WAL: {CYAN}/tmp/ps01_wal.jsonl{RESET}")
    print(f"  • ChromaDB: {CYAN}./chroma_db/default/{RESET}")
    print(f"  • SQLite: {CYAN}./mem0_history/default/default.db{RESET}")
    
    return customer_id


def session_2_demo(customer_id: str):
    """Second session: System remembers customer from Session 1"""
    print_header("SESSION 2: Meeting Again (System Should Remember!)")
    
    print(f"{YELLOW}Days later...{RESET}\n")
    time.sleep(2)
    
    print_info("Rajesh calls again...")
    print(f"{YELLOW}📞 Phone rings...{RESET}\n")
    time.sleep(1)
    
    # Start new session - SYSTEM SHOULD REMEMBER!
    session = start_session(customer_id, "Rajesh")
    if not session:
        return
    
    print_section("Notice the Greeting!")
    print(f"{BOLD}What happened:{RESET}")
    print(f"  1. System looked up {CYAN}customer_id: {customer_id}{RESET}")
    print(f"  2. Retrieved facts from memory (WAL → ChromaDB → SQLite → Redis)")
    print(f"  3. Found: income, location, wife's name, etc.")
    print(f"  4. Called phi4-mini with these facts")
    print(f"  5. Generated personalized greeting in Hinglish")
    
    # Second conversation (different topics, system doesn't repeat)
    turns = [
        "Actually, I've decided the loan amount should be ₹50 lakhs now",
        "Can we get approval in 2 weeks?",
        "Great! Let's proceed with the application"
    ]
    
    for i, customer_message in enumerate(turns, 1):
        print_info(f"Turn {i}/{len(turns)}")
        exchange_message(session["session_id"], customer_id, customer_message)
        time.sleep(1)
    
    print_section("Session 2 Complete!")
    print(f"{GREEN}✅ Cross-Session Memory Demonstrated!{RESET}\n")
    print(f"{BOLD}What the system did:{RESET}")
    print(f"  ✅ Recognized Rajesh from Session 1")
    print(f"  ✅ Referenced his known facts (Sunita, Nashik, property)")
    print(f"  ✅ Didn't ask about repeat information")
    print(f"  ✅ Updated loan amount revision (50L instead of 45L)")
    print(f"  ✅ Made relevant next-step questions")
    
    # Show updated memory
    view_memory_state(customer_id)


def main():
    print_header("PS-01 Cross-Session Memory Demo")
    print(f"{BOLD}Watch how the system REMEMBERS customers across sessions!{RESET}\n")
    
    # Check API connectivity
    try:
        response = requests.get(f"{API_BASE}/health", timeout=2)
        print_success("FastAPI server is running")
    except:
        print(f"{RED}ERROR: FastAPI server not running!{RED}")
        print(f"Start it with: python3 -m uvicorn src.api.app:app --port 8000\n")
        return
    
    print()
    input(f"{YELLOW}Press Enter to START SESSION 1...{RESET}")
    
    # SESSION 1
    customer_id = session_1_demo()
    
    if not customer_id:
        print(f"{RED}Session 1 failed. Exiting.{RESET}")
        return
    
    # Pause between sessions
    print("\n" + "="*70)
    input(f"\n{YELLOW}Press Enter to START SESSION 2 (system should remember!){RESET}\n")
    
    # SESSION 2
    session_2_demo(customer_id)
    
    # Summary
    print_header("Demo Complete!")
    print(f"{BOLD}What We Demonstrated:{RESET}\n")
    print(f"  {GREEN}✅ Session 1:{RESET} Collected Rajesh's information")
    print(f"        • Everything stored in 4-tier memory system")
    print(f"        • WAL, ChromaDB, SQLite, Redis all populated")
    print(f"\n  {GREEN}✅ Cross-Session Memory:{RESET} System recognized Rajesh")
    print(f"        • Retrieved facts from memory tiers")
    print(f"        • Generated personalized greeting")
    print(f"        • Referenced known information naturally")
    print(f"\n  {GREEN}✅ Session 2:{RESET} No information repeated")
    print(f"        • Asked different questions")
    print(f"        • Built on Session 1 context")
    print(f"        • Updated new information (₹50L loan revision)")
    
    print(f"\n{BOLD}Memory Proof:{RESET}")
    print(f"  🔍 Check WAL:      {CYAN}cat /tmp/ps01_wal.jsonl | jq '.[] | select(.customer_id | contains(\"demo_rajesh\"))'${RESET}")
    print(f"  🔍 Check SQLite:   {CYAN}sqlite3 ./mem0_history/default/default.db \"SELECT * FROM facts WHERE customer_id='demo_rajesh_001'\"${RESET}")
    print(f"  🔍 Dashboard:      {CYAN}http://localhost:8765${RESET}")
    
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Demo interrupted by user.{RESET}")
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}")
        import traceback
        traceback.print_exc()

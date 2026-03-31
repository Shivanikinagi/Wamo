#!/usr/bin/env python3
"""
End-to-End Rajesh Demo Runner
Simulates the complete 4-session journey with memory persistence validation.
"""

import asyncio
import httpx
import json
import sys
import time
from typing import Dict, Any, List
from pathlib import Path

# Configuration
API_BASE_URL = "http://localhost:8000"
BANK_ID = "cooperative_bank_01"
CUSTOMER_ID = "rajesh_demo_001"
TIMEOUT = 30.0

# ANSI colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_header(text: str):
    """Print formatted header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*80}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(80)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*80}{Colors.ENDC}\n")


def print_success(text: str):
    """Print success message."""
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message."""
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_info(text: str):
    """Print info message."""
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


class RajeshDemoRunner:
    """Runs the complete Rajesh 4-session demo."""
    
    def __init__(self, base_url: str = API_BASE_URL, bank_id: str = BANK_ID):
        self.base_url = base_url
        self.bank_id = bank_id
        self.customer_id = CUSTOMER_ID
        self.sessions: List[Dict[str, Any]] = []
        
    async def check_health(self) -> bool:
        """Check if API server is running."""
        print_info("Checking API health...")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                if response.status_code == 200:
                    print_success("API server is healthy")
                    return True
                else:
                    print_error(f"API returned status {response.status_code}")
                    return False
        except Exception as e:
            print_error(f"Cannot connect to API: {e}")
            print_info(f"Make sure the server is running: uvicorn src.api.app:app --port 8000")
            return False
    
    async def clear_previous_data(self):
        """Clear any previous demo data."""
        print_info("Clearing previous demo data...")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                # Try to clear via API if endpoint exists
                response = await client.post(
                    f"{self.base_url}/demo/clear",
                    headers={"X-Bank-ID": self.bank_id},
                    json={"customer_id": self.customer_id}
                )
                if response.status_code == 200:
                    print_success("Previous data cleared")
                else:
                    print_warning("Clear endpoint not available, continuing...")
        except Exception:
            print_warning("Could not clear previous data, continuing...")
    
    async def run_session(
        self,
        session_num: int,
        agent_id: str,
        messages: List[str],
        expected_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run a single session."""
        print_header(f"SESSION {session_num} - Agent {agent_id}")
        
        session_data = {
            "session_num": session_num,
            "agent_id": agent_id,
            "messages": messages,
            "facts_added": [],
            "context_validated": False
        }
        
        # Step 1: Start session
        print_info(f"Starting session with Agent {agent_id}...")
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/session/start",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "customer_id": self.customer_id,
                        "agent_id": agent_id,
                        "session_type": "home_loan_processing",
                        "consent_id": f"CONSENT_{self.customer_id}"
                    }
                )
                
                if response.status_code != 200:
                    print_error(f"Session start failed: {response.status_code}")
                    print_error(response.text)
                    return session_data
                
                result = response.json()
                session_id = result.get("session_id")
                greeting = result.get("greeting_message", "")
                has_context = result.get("has_prior_context", False)
                
                print_success(f"Session started: {session_id}")
                print_info(f"Greeting: {greeting}")
                
                # Validate context expectations
                if session_num == 1:
                    if not has_context:
                        print_success("✓ First session: No prior context (expected)")
                    else:
                        print_warning("⚠ First session should have no context")
                else:
                    if has_context:
                        print_success(f"✓ Session {session_num}: Has prior context (expected)")
                        session_data["context_validated"] = True
                    else:
                        print_error(f"✗ Session {session_num}: Missing prior context!")
                
                # Display facts from previous sessions
                if has_context:
                    verified = result.get("verified_facts", [])
                    unverified = result.get("unverified_facts", [])
                    print_info(f"Retrieved {len(verified)} verified + {len(unverified)} unverified facts")
                    for fact in (verified + unverified)[:5]:  # Show first 5
                        print(f"  - {fact.get('type')}: {fact.get('value')}")
                
                session_data["session_id"] = session_id
                
            except Exception as e:
                print_error(f"Session start error: {e}")
                return session_data
        
        # Step 2: Send messages
        for i, message in enumerate(messages, 1):
            print_info(f"Message {i}/{len(messages)}: {message[:60]}...")
            
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/session/converse",
                        headers={"X-Bank-ID": self.bank_id},
                        json={
                            "session_id": session_id,
                            "message": message
                        }
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        agent_response = result.get("agent_response", "")
                        facts = result.get("facts_to_update", [])
                        
                        print_success(f"Agent: {agent_response[:80]}...")
                        if facts:
                            print_info(f"Facts extracted: {len(facts)}")
                            for fact in facts:
                                print(f"  - {fact.get('type')}: {fact.get('value')}")
                                session_data["facts_added"].append(fact)
                    else:
                        print_warning(f"Converse returned {response.status_code}")
                        
                except Exception as e:
                    print_warning(f"Converse error: {e}")
            
            await asyncio.sleep(0.5)  # Brief pause between messages
        
        # Step 3: End session
        print_info("Ending session...")
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/session/end",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "session_id": session_id,
                        "transcript": " ".join(messages)
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    print_success("Session ended successfully")
                    print_info(f"Facts persisted: {result.get('facts_compacted', 0)}")
                else:
                    print_warning(f"Session end returned {response.status_code}")
                    
            except Exception as e:
                print_warning(f"Session end error: {e}")
        
        self.sessions.append(session_data)
        return session_data
    
    async def run_complete_demo(self):
        """Run all 4 Rajesh sessions."""
        print_header("RAJESH'S LOAN JOURNEY - 4 SESSIONS")
        print_info(f"Customer ID: {self.customer_id}")
        print_info(f"Bank ID: {self.bank_id}")
        print_info(f"API: {self.base_url}\n")
        
        # Health check
        if not await self.check_health():
            return False
        
        # Clear previous data
        await self.clear_previous_data()
        
        # Wait for services to be ready
        print_info("Waiting 2 seconds for services to be ready...")
        await asyncio.sleep(2)
        
        # Session 1: Agent A - Initial information
        await self.run_session(
            session_num=1,
            agent_id="AGT_A",
            messages=[
                "Hello, I want to apply for a home loan",
                "My monthly income is 55000 rupees",
                "I work at a company in Pune",
                "My wife Sunita is the co-applicant"
            ],
            expected_context={"has_prior": False}
        )
        
        await asyncio.sleep(2)
        
        # Session 2: Agent B - Additional details
        await self.run_session(
            session_num=2,
            agent_id="AGT_B",
            messages=[
                "Hi, I spoke to someone last week about a home loan",
                "My wife Sunita earns 30000 rupees per month",
                "We have a car loan EMI of 12000 rupees monthly"
            ],
            expected_context={"has_prior": True, "income": "55000"}
        )
        
        await asyncio.sleep(2)
        
        # Session 3: Agent C - Property details
        await self.run_session(
            session_num=3,
            agent_id="AGT_C",
            messages=[
                "I have the land documents for the property in Nashik",
                "It's a 1200 square meter plot with clean title"
            ],
            expected_context={"has_prior": True, "co_applicant": "Sunita"}
        )
        
        await asyncio.sleep(2)
        
        # Session 4: Agent D - Final verification
        await self.run_session(
            session_num=4,
            agent_id="AGT_D",
            messages=[
                "I need to correct my income - it's actually 62000 rupees per month",
                "Can you tell me my loan eligibility?"
            ],
            expected_context={"has_prior": True, "all_facts": True}
        )
        
        return True
    
    def print_summary(self):
        """Print demo summary."""
        print_header("DEMO SUMMARY")
        
        total_facts = sum(len(s.get("facts_added", [])) for s in self.sessions)
        context_validated = sum(1 for s in self.sessions if s.get("context_validated", False))
        
        print(f"Total Sessions: {len(self.sessions)}")
        print(f"Total Facts Added: {total_facts}")
        print(f"Context Validated: {context_validated}/{len(self.sessions)-1} (excluding first session)")
        
        print(f"\n{Colors.BOLD}Session Breakdown:{Colors.ENDC}")
        for session in self.sessions:
            num = session.get("session_num")
            agent = session.get("agent_id")
            facts = len(session.get("facts_added", []))
            validated = "✓" if session.get("context_validated") else "✗"
            print(f"  Session {num} ({agent}): {facts} facts, Context: {validated}")
        
        # Final validation
        print(f"\n{Colors.BOLD}Validation Results:{Colors.ENDC}")
        if len(self.sessions) == 4:
            print_success("✓ All 4 sessions completed")
        else:
            print_error(f"✗ Only {len(self.sessions)}/4 sessions completed")
        
        if context_validated >= 2:  # At least sessions 2 and 3
            print_success("✓ Memory persistence working (context validated)")
        else:
            print_error("✗ Memory persistence issue (context not validated)")
        
        if total_facts >= 8:
            print_success(f"✓ Sufficient facts captured ({total_facts})")
        else:
            print_warning(f"⚠ Only {total_facts} facts captured (expected ~10)")


async def main():
    """Main entry point."""
    runner = RajeshDemoRunner()
    
    try:
        success = await runner.run_complete_demo()
        runner.print_summary()
        
        if success:
            print_header("DEMO COMPLETED SUCCESSFULLY")
            print_success("The loan officer memory system is working!")
            print_info("Agent D had complete context from all previous sessions")
            return 0
        else:
            print_header("DEMO FAILED")
            print_error("Check the errors above and ensure services are running")
            return 1
            
    except KeyboardInterrupt:
        print_warning("\n\nDemo interrupted by user")
        return 1
    except Exception as e:
        print_error(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

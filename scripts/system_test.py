#!/usr/bin/env python3
"""
System Test Suite for PS-01
Tests all components end-to-end without mocking.
"""

import asyncio
import httpx
import json
import sys
import os
from pathlib import Path
from typing import Dict, Any, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
BANK_ID = os.getenv("BANK_ID", "cooperative_bank_01")
TEST_CUSTOMER_ID = "test_customer_system_001"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


class SystemTest:
    """System-level integration tests."""
    
    def __init__(self):
        self.base_url = API_BASE_URL
        self.bank_id = BANK_ID
        self.customer_id = TEST_CUSTOMER_ID
        self.passed = 0
        self.failed = 0
        self.tests_run = []
    
    def print_test(self, name: str, passed: bool, message: str = ""):
        """Print test result."""
        status = f"{Colors.GREEN}PASS{Colors.ENDC}" if passed else f"{Colors.RED}FAIL{Colors.ENDC}"
        print(f"  [{status}] {name}")
        if message:
            print(f"        {message}")
        
        self.tests_run.append({"name": name, "passed": passed, "message": message})
        if passed:
            self.passed += 1
        else:
            self.failed += 1
    
    async def test_health_endpoint(self):
        """Test 1: Health endpoint responds."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                passed = response.status_code == 200
                self.print_test(
                    "Health endpoint",
                    passed,
                    f"Status: {response.status_code}"
                )
                return passed
        except Exception as e:
            self.print_test("Health endpoint", False, str(e))
            return False
    
    async def test_session_start(self) -> str:
        """Test 2: Session can be started."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/session/start",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "customer_id": self.customer_id,
                        "agent_id": "TEST_AGENT",
                        "session_type": "home_loan_processing",
                        "consent_id": f"CONSENT_{self.customer_id}"
                    }
                )
                
                passed = response.status_code == 200
                session_id = ""
                if passed:
                    result = response.json()
                    session_id = result.get("session_id", "")
                    passed = bool(session_id)
                
                self.print_test(
                    "Session start",
                    passed,
                    f"Session ID: {session_id[:20]}..." if session_id else "No session ID"
                )
                return session_id if passed else ""
        except Exception as e:
            self.print_test("Session start", False, str(e))
            return ""
    
    async def test_session_converse(self, session_id: str):
        """Test 3: Can send message in session."""
        if not session_id:
            self.print_test("Session converse", False, "No session ID")
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/session/converse",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "session_id": session_id,
                        "message": "My monthly income is 50000 rupees"
                    }
                )
                
                passed = response.status_code == 200
                agent_response = ""
                if passed:
                    result = response.json()
                    agent_response = result.get("agent_response", "")
                    passed = bool(agent_response)
                
                self.print_test(
                    "Session converse",
                    passed,
                    f"Response: {agent_response[:50]}..." if agent_response else "No response"
                )
                return passed
        except Exception as e:
            self.print_test("Session converse", False, str(e))
            return False
    
    async def test_session_end(self, session_id: str):
        """Test 4: Session can be ended."""
        if not session_id:
            self.print_test("Session end", False, "No session ID")
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/session/end",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "session_id": session_id,
                        "transcript": "Test transcript"
                    }
                )
                
                passed = response.status_code == 200
                self.print_test(
                    "Session end",
                    passed,
                    f"Status: {response.status_code}"
                )
                return passed
        except Exception as e:
            self.print_test("Session end", False, str(e))
            return False
    
    async def test_memory_persistence(self):
        """Test 5: Memory persists across sessions."""
        try:
            # Session 1: Add fact
            async with httpx.AsyncClient(timeout=10.0) as client:
                response1 = await client.post(
                    f"{self.base_url}/session/start",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "customer_id": f"{self.customer_id}_mem",
                        "agent_id": "AGENT_1",
                        "session_type": "home_loan_processing",
                        "consent_id": f"CONSENT_{self.customer_id}_mem"
                    }
                )
                
                if response1.status_code != 200:
                    self.print_test("Memory persistence", False, "Session 1 start failed")
                    return False
                
                session1_id = response1.json().get("session_id")
                
                # Add a fact
                await client.post(
                    f"{self.base_url}/session/converse",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "session_id": session1_id,
                        "message": "My income is 75000 rupees per month"
                    }
                )
                
                # End session 1
                await client.post(
                    f"{self.base_url}/session/end",
                    headers={"X-Bank-ID": self.bank_id},
                    json={"session_id": session1_id, "transcript": "Session 1"}
                )
                
                # Wait for persistence
                await asyncio.sleep(2)
                
                # Session 2: Check if fact is retrieved
                response2 = await client.post(
                    f"{self.base_url}/session/start",
                    headers={"X-Bank-ID": self.bank_id},
                    json={
                        "customer_id": f"{self.customer_id}_mem",
                        "agent_id": "AGENT_2",
                        "session_type": "home_loan_processing",
                        "consent_id": f"CONSENT_{self.customer_id}_mem"
                    }
                )
                
                if response2.status_code != 200:
                    self.print_test("Memory persistence", False, "Session 2 start failed")
                    return False
                
                result2 = response2.json()
                has_context = result2.get("has_prior_context", False)
                facts = result2.get("verified_facts", []) + result2.get("unverified_facts", [])
                
                passed = has_context and len(facts) > 0
                self.print_test(
                    "Memory persistence",
                    passed,
                    f"Context: {has_context}, Facts: {len(facts)}"
                )
                return passed
                
        except Exception as e:
            self.print_test("Memory persistence", False, str(e))
            return False
    
    async def test_wal_integrity(self):
        """Test 6: WAL file is being written."""
        try:
            wal_path = Path("data/wal/ps01_wal.jsonl")
            if not wal_path.exists():
                wal_path = Path("wal.jsonl")
            
            if not wal_path.exists():
                self.print_test("WAL integrity", False, "WAL file not found")
                return False
            
            # Check if WAL has entries
            with open(wal_path, 'r') as f:
                lines = f.readlines()
            
            passed = len(lines) > 0
            self.print_test(
                "WAL integrity",
                passed,
                f"WAL entries: {len(lines)}"
            )
            return passed
        except Exception as e:
            self.print_test("WAL integrity", False, str(e))
            return False
    
    async def test_pii_tokenization(self):
        """Test 7: PII is tokenized."""
        try:
            from src.preprocessing.tokenizer import BankingTokenizer
            
            tokenizer = BankingTokenizer()
            text = "My PAN is ABCDE1234F and Aadhaar is 123456789012"
            tokenized, mapping = tokenizer.tokenize(text)
            
            # Check that PAN and Aadhaar are replaced
            passed = (
                "ABCDE1234F" not in tokenized and
                "123456789012" not in tokenized and
                "[TOKEN:PAN:" in tokenized and
                "[TOKEN:AADHAAR:" in tokenized
            )
            
            self.print_test(
                "PII tokenization",
                passed,
                f"Tokens: {len(mapping)}"
            )
            return passed
        except Exception as e:
            self.print_test("PII tokenization", False, str(e))
            return False
    
    async def test_conflict_detection(self):
        """Test 8: Conflict detection works."""
        try:
            from src.core.conflict_detector import ConflictDetector
            
            detector = ConflictDetector()
            existing = [{"type": "income", "value": "50000"}]
            new = [{"type": "income", "value": "80000"}]
            
            conflicts = detector.detect(existing, new)
            passed = len(conflicts) > 0
            
            self.print_test(
                "Conflict detection",
                passed,
                f"Conflicts found: {len(conflicts)}"
            )
            return passed
        except Exception as e:
            self.print_test("Conflict detection", False, str(e))
            return False
    
    async def run_all_tests(self):
        """Run all system tests."""
        print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}PS-01 System Test Suite{Colors.ENDC}")
        print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}\n")
        
        print(f"{Colors.BLUE}Running tests...{Colors.ENDC}\n")
        
        # Test 1: Health
        health_ok = await self.test_health_endpoint()
        if not health_ok:
            print(f"\n{Colors.RED}API server not available. Stopping tests.{Colors.ENDC}")
            return False
        
        # Test 2-4: Basic session flow
        session_id = await self.test_session_start()
        if session_id:
            await self.test_session_converse(session_id)
            await self.test_session_end(session_id)
        
        # Test 5: Memory persistence
        await self.test_memory_persistence()
        
        # Test 6: WAL
        await self.test_wal_integrity()
        
        # Test 7-8: Component tests
        await self.test_pii_tokenization()
        await self.test_conflict_detection()
        
        # Summary
        print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}Test Summary{Colors.ENDC}")
        print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}\n")
        
        total = self.passed + self.failed
        pass_rate = (self.passed / total * 100) if total > 0 else 0
        
        print(f"Total Tests: {total}")
        print(f"{Colors.GREEN}Passed: {self.passed}{Colors.ENDC}")
        print(f"{Colors.RED}Failed: {self.failed}{Colors.ENDC}")
        print(f"Pass Rate: {pass_rate:.1f}%\n")
        
        if self.failed == 0:
            print(f"{Colors.GREEN}{Colors.BOLD}✓ All tests passed!{Colors.ENDC}\n")
            return True
        else:
            print(f"{Colors.RED}{Colors.BOLD}✗ Some tests failed{Colors.ENDC}\n")
            return False


async def main():
    """Main entry point."""
    test = SystemTest()
    
    try:
        success = await test.run_all_tests()
        return 0 if success else 1
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted{Colors.ENDC}")
        return 1
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.ENDC}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

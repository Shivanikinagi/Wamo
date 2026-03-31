# User Journey Comparison: New vs Returning Customer

## Visual Flow Comparison

### 🆕 NEW USER JOURNEY

```
┌─────────────────────────────────────────────────────────────┐
│ SESSION 1 - First Time Customer                             │
│ Customer: Rajesh (new_customer_001)                         │
│ Agent: Agent A                                              │
└─────────────────────────────────────────────────────────────┘

Step 1: Session Start
─────────────────────
POST /session/start
  customer_id: "new_customer_001"
  agent_id: "AGT_A"

System Checks:
  ├─ WAL entries for customer? → NONE ❌
  ├─ Mem0 memories? → NONE ❌
  └─ Redis cache? → NONE ❌

Response:
  ├─ has_prior_context: FALSE
  ├─ verified_facts: []
  ├─ unverified_facts: []
  ├─ greeting: "Welcome! How can I help you today?"
  └─ suggested_next: "Collect customer information"

Step 2: Conversation
────────────────────
Agent A: "What type of loan are you looking for?"
Rajesh: "Home loan"

Agent A: "What is your monthly income?"
Rajesh: "50000 rupees"

Agent A: "Do you have a co-applicant?"
Rajesh: "Yes, my wife Sunita"

System Stores:
  ├─ WAL: [income: 50000, loan_type: home, co_applicant: Sunita]
  ├─ Mem0: Facts added to vector store
  └─ Redis: Session state cached

Step 3: Session End
───────────────────
POST /session/end

System:
  ├─ Compacts facts
  ├─ Caches summary
  └─ Customer is now "KNOWN" ✅

┌─────────────────────────────────────────────────────────────┐
│ RESULT: Customer data stored, ready for next session        │
└─────────────────────────────────────────────────────────────┘
```

### 🔄 RETURNING USER JOURNEY

```
┌─────────────────────────────────────────────────────────────┐
│ SESSION 2 - Returning Customer                              │
│ Customer: Rajesh (new_customer_001) ← SAME ID               │
│ Agent: Agent B ← DIFFERENT AGENT                            │
└─────────────────────────────────────────────────────────────┘

Step 1: Session Start
─────────────────────
POST /session/start
  customer_id: "new_customer_001"  ← SAME customer
  agent_id: "AGT_B"  ← DIFFERENT agent

System Checks:
  ├─ WAL entries for customer? → FOUND ✅
  │   └─ 3 facts from Session 1
  ├─ Mem0 memories? → FOUND ✅
  │   └─ income, loan_type, co_applicant
  └─ Redis cache? → FOUND ✅
      └─ Cached summary

Response:
  ├─ has_prior_context: TRUE ✅
  ├─ verified_facts: []
  ├─ unverified_facts: [
  │     {type: "income", value: "50000"},
  │     {type: "loan_type", value: "home"},
  │     {type: "co_applicant", value: "Sunita"}
  │   ]
  ├─ greeting: "Welcome back! I see you discussed income of ₹50,000 previously."
  └─ suggested_next: "Continue with loan application"

Step 2: Conversation
────────────────────
Agent B: "I see you're looking for a home loan with income ₹50,000"
          ↑ Agent B KNOWS this without asking!

Rajesh: "Yes, that's correct"
        ↑ Rajesh doesn't need to repeat!

Agent B: "Do you have property documents?"
Rajesh: "Yes, I have land documents for Nashik plot"

System Stores:
  ├─ WAL: [property_location: Nashik, document_type: land]
  ├─ Mem0: NEW facts added (keeps old facts too)
  └─ Redis: Updated summary

Step 3: Session End
───────────────────
POST /session/end

System:
  ├─ Compacts ALL facts (old + new)
  ├─ Updates cached summary
  └─ Customer history grows ✅

┌─────────────────────────────────────────────────────────────┐
│ RESULT: Agent B had full context, no repeated questions     │
└─────────────────────────────────────────────────────────────┘
```

## Side-by-Side Comparison

| Aspect | New User (Session 1) | Returning User (Session 2+) |
|--------|---------------------|----------------------------|
| **Customer ID** | `new_customer_001` | `new_customer_001` (same) |
| **Agent** | Agent A | Agent B (different) |
| **has_prior_context** | `false` ❌ | `true` ✅ |
| **Facts Available** | None `[]` | Previous facts `[...]` |
| **Greeting** | Generic welcome | Personalized with context |
| **Agent Behavior** | Asks ALL questions | Skips known information |
| **Customer Experience** | Provides all info | Only provides NEW info |
| **Questions Asked** | 10-15 questions | 3-5 questions |
| **Time Spent** | 15-20 minutes | 5-10 minutes |
| **Frustration Level** | Low (first time) | **ZERO** (no repetition!) |

## Real-World Example

### Scenario: Rajesh's 4 Sessions

```
SESSION 1 (Day 1) - Agent A
├─ Status: NEW USER
├─ has_prior_context: false
├─ Questions: 12
├─ Time: 18 minutes
└─ Facts Stored: income, employer, co-applicant

SESSION 2 (Day 7) - Agent B
├─ Status: RETURNING USER ✅
├─ has_prior_context: true
├─ Questions: 4 (only new info)
├─ Time: 8 minutes
└─ Facts Added: EMI, co-applicant income

SESSION 3 (Day 14) - Agent C
├─ Status: RETURNING USER ✅
├─ has_prior_context: true
├─ Questions: 2 (only documents)
├─ Time: 5 minutes
└─ Facts Added: property documents

SESSION 4 (Day 21) - Agent D
├─ Status: RETURNING USER ✅
├─ has_prior_context: true
├─ Questions: 0 (has everything!)
├─ Time: 3 minutes
└─ Action: Final verification only
```

### Time Savings

**Without PS-01** (Traditional System):
- Session 1: 18 min (asks everything)
- Session 2: 18 min (asks everything AGAIN)
- Session 3: 18 min (asks everything AGAIN)
- Session 4: 18 min (asks everything AGAIN)
- **Total: 72 minutes** 😫

**With PS-01** (Memory System):
- Session 1: 18 min (first time)
- Session 2: 8 min (remembers Session 1)
- Session 3: 5 min (remembers Sessions 1+2)
- Session 4: 3 min (remembers everything)
- **Total: 34 minutes** 🎉

**Savings: 38 minutes (53% reduction!)**

## Technical Flow

### New User Detection

```python
# In briefing_builder.py
all_entries = self.wal_logger.get_all_for_customer(customer_id)

if len(all_entries) == 0:
    # NEW USER
    briefing = {
        "has_prior_context": False,
        "session_count": 0,
        "facts": [],
        "recommended_next_step": "Collect customer information"
    }
else:
    # RETURNING USER
    briefing = {
        "has_prior_context": True,
        "session_count": len(all_entries),
        "facts": extract_facts_from_entries(all_entries),
        "recommended_next_step": "Continue with loan application"
    }
```

### Memory Persistence

```python
# Session 1 (New User)
POST /session/start → has_prior_context: false
POST /session/converse → Facts stored to WAL + Mem0
POST /session/end → Summary cached

# Session 2 (Returning User)
POST /session/start → has_prior_context: true
                   → Facts retrieved from WAL + Mem0
                   → Agent sees previous context
```

## Key Takeaways

### For New Users:
1. ✅ System starts with clean slate
2. ✅ No assumptions made
3. ✅ All information collected
4. ✅ Facts stored for future sessions
5. ✅ Smooth onboarding experience

### For Returning Users:
1. ✅ Full context retrieved instantly
2. ✅ No repeated questions
3. ✅ Personalized experience
4. ✅ Time savings (50%+)
5. ✅ Better customer satisfaction

### For Agents:
1. ✅ Clear indication (has_prior_context flag)
2. ✅ Previous facts displayed
3. ✅ Suggested next steps
4. ✅ Can pick up where others left off
5. ✅ More efficient workflow

## Testing Both Flows

### Test New User Flow
```bash
# Run demo with fresh customer ID
python3 scripts/run_rajesh_demo.py

# Session 1 will show:
# ✓ First session: No prior context (expected)
```

### Test Returning User Flow
```bash
# Same demo continues
# Sessions 2-4 will show:
# ✓ Session 2: Has prior context (expected)
# ✓ Session 3: Has prior context (expected)
# ✓ Session 4: Has prior context (expected)
```

## Conclusion

**PS-01 seamlessly handles both new and returning customers:**

- **New users** get a clean, professional onboarding
- **Returning users** get instant context and no repetition
- **Agents** know immediately which type of customer they're dealing with
- **System** automatically manages the transition from new to returning

**This is the core value proposition: Memory that works for everyone!**

---

**Last Updated**: March 30, 2026
**Status**: Fully implemented and tested

#!/usr/bin/env python3
"""
Memory Storage Inspector — View ChromaDB, SQLite, and WAL data
Run: python3 scripts/inspect_memory_storage.py [--chromadb | --sqlite | --wal | --all]
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime
import subprocess

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db" / "default"
MEM0_HISTORY_PATH = PROJECT_ROOT / "mem0_history" / "default"
WAL_PATH = Path("/tmp/ps01_wal.jsonl")

# Colors for terminal output
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(title):
    print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}{title.center(70)}{RESET}")
    print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")


def print_subheader(title):
    print(f"\n{YELLOW}📌 {title}{RESET}")
    print(f"{YELLOW}{'-'*70}{RESET}")


def inspect_chromadb():
    """View ChromaDB vector store contents"""
    print_header("ChromaDB Vector Store")
    
    chroma_sqlite = CHROMA_DB_PATH / "chroma.sqlite3"
    
    if not chroma_sqlite.exists():
        print(f"{RED}❌ ChromaDB not found at {chroma_sqlite}{RESET}")
        return
    
    print(f"{GREEN}✅ ChromaDB found: {chroma_sqlite}{RESET}")
    print(f"   File size: {chroma_sqlite.stat().st_size / 1024:.1f}KB")
    
    try:
        conn = sqlite3.connect(str(chroma_sqlite))
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        print(f"\n{BLUE}Tables in ChromaDB:{RESET}")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]};")
            count = cursor.fetchone()[0]
            print(f"  • {MAGENTA}{table[0]}{RESET}: {GREEN}{count}{RESET} rows")
        
        # Show collections
        print_subheader("Collections (Vector Collections)")
        cursor.execute("SELECT * FROM collections;")
        collections = cursor.fetchall()
        
        if collections:
            cursor.execute("PRAGMA table_info(collections);")
            columns = [col[1] for col in cursor.fetchall()]
            
            for idx, collection in enumerate(collections, 1):
                print(f"\n  {MAGENTA}Collection {idx}:{RESET}")
                for col, val in zip(columns, collection):
                    print(f"    {CYAN}{col:20s}{RESET} → {YELLOW}{val}{RESET}")
        else:
            print(f"  {YELLOW}(No collections yet){RESET}")
        
        # Show embeddings sample — use actual schema columns
        print_subheader("Embeddings (Vector Data Sample)")
        cursor.execute("PRAGMA table_info(embeddings);")
        emb_cols = [r[1] for r in cursor.fetchall()]
        cursor.execute(f"SELECT * FROM embeddings LIMIT 3;")
        embeddings = cursor.fetchall()

        if embeddings:
            for idx, row in enumerate(embeddings, 1):
                row_dict = dict(zip(emb_cols, row))
                print(f"\n  {MAGENTA}Embedding {idx}:{RESET}")
                for col, val in row_dict.items():
                    display = str(val)[:80] if val is not None else "NULL"
                    print(f"    {CYAN}{col:20s}{RESET} → {YELLOW}{display}{RESET}")
                # fetch associated metadata
                emb_id = row_dict.get("id")
                if emb_id is not None:
                    cursor.execute(
                        "SELECT key, string_value FROM embedding_metadata WHERE id=?;",
                        (emb_id,)
                    )
                    meta = cursor.fetchall()
                    if meta:
                        print(f"    {CYAN}{'metadata':20s}{RESET}", end="")
                        for k, v in meta:
                            print(f" {GREEN}{k}{RESET}={YELLOW}{v}{RESET}", end="")
                        print()
        else:
            print(f"  {YELLOW}(No embeddings yet){RESET}")
        
        conn.close()
        
    except Exception as e:
        print(f"{RED}❌ Error reading ChromaDB: {e}{RESET}")


def inspect_mem0_sqlite():
    """View Mem0 SQLite database contents"""
    print_header("Mem0 SQLite Metadata Store")
    
    mem0_db = MEM0_HISTORY_PATH / "default.db"
    
    if not mem0_db.exists():
        print(f"{RED}❌ Mem0 database not found at {mem0_db}{RESET}")
        return
    
    print(f"{GREEN}✅ Mem0 database found: {mem0_db}{RESET}")
    print(f"   File size: {mem0_db.stat().st_size / 1024:.1f}KB")
    
    try:
        conn = sqlite3.connect(str(mem0_db))
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        print(f"\n{BLUE}Tables in Mem0:{RESET}")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]};")
            count = cursor.fetchone()[0]
            print(f"  • {MAGENTA}{table[0]}{RESET}: {GREEN}{count}{RESET} rows")
        
        # Show memories/facts
        print_subheader("Memory Facts (Main Data)")
        
        # Try different table names (memory_facts, facts, memories)
        table_name = None
        for name in ["memory_facts", "facts", "memories", "memory"]:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{name}';")
            if cursor.fetchone():
                table_name = name
                break
        
        if not table_name:
            print(f"  {YELLOW}(No memory facts table found){RESET}")
        else:
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = [col[1] for col in cursor.fetchall()]
            
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
            facts = cursor.fetchall()
            
            if facts:
                print(f"\n  {BLUE}Columns: {', '.join(columns)}{RESET}\n")
                for idx, fact in enumerate(facts, 1):
                    print(f"  {MAGENTA}Fact {idx}:{RESET}")
                    for col, val in zip(columns, fact):
                        if val and len(str(val)) > 80:
                            print(f"    {CYAN}{col:20s}{RESET} → {YELLOW}{str(val)[:80]}...{RESET}")
                        else:
                            print(f"    {CYAN}{col:20s}{RESET} → {YELLOW}{val}{RESET}")
                    print()
            else:
                print(f"  {YELLOW}(No facts yet){RESET}")
        
        # Show relationships if they exist
        print_subheader("Relationships (if any)")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%relation%';")
        relation_tables = cursor.fetchall()
        
        if relation_tables:
            for rel_table in relation_tables:
                rel_name = rel_table[0]
                cursor.execute(f"SELECT COUNT(*) FROM {rel_name};")
                count = cursor.fetchone()[0]
                print(f"  • {MAGENTA}{rel_name}{RESET}: {GREEN}{count}{RESET} relationships")
        else:
            print(f"  {YELLOW}(No relationship tables found){RESET}")
        
        conn.close()
        
    except Exception as e:
        print(f"{RED}❌ Error reading Mem0 database: {e}{RESET}")


def inspect_wal():
    """View Write-Ahead Log entries"""
    print_header("Write-Ahead Log (WAL)")
    
    if not WAL_PATH.exists():
        print(f"{RED}❌ WAL not found at {WAL_PATH}{RESET}")
        return
    
    print(f"{GREEN}✅ WAL found: {WAL_PATH}{RESET}")
    
    try:
        with open(WAL_PATH, 'r') as f:
            lines = f.readlines()
        
        print(f"   File size: {WAL_PATH.stat().st_size / 1024:.1f}KB")
        print(f"   Total entries: {GREEN}{len(lines)}{RESET}")
        
        if not lines:
            print(f"\n  {YELLOW}(WAL is empty){RESET}")
            return
        
        print_subheader("Latest WAL Entries (last 5)")

        for idx, line in enumerate(lines[-5:], 1):
            try:
                entry = json.loads(line)
                abs_idx = len(lines) - 5 + idx
                print(f"\n  {MAGENTA}Entry {abs_idx}:{RESET}")
                print(f"    {CYAN}Timestamp  {RESET} → {YELLOW}{entry.get('timestamp', 'N/A')}{RESET}")
                print(f"    {CYAN}Customer ID{RESET} → {YELLOW}{entry.get('customer_id', 'N/A')}{RESET}")
                print(f"    {CYAN}Session ID {RESET} → {YELLOW}{entry.get('session_id', 'N/A')}{RESET}")
                print(f"    {CYAN}Shipped    {RESET} → {YELLOW}{entry.get('shipped', 'N/A')}{RESET}")
                # facts is a list of {type, value, ...} objects
                facts = entry.get("facts", [])
                if facts:
                    print(f"    {CYAN}Facts ({len(facts)}){RESET}")
                    for f in facts:
                        ftype  = f.get("type", "?")
                        fvalue = str(f.get("value", "?"))[:60]
                        src    = f.get("source", "")
                        print(f"      {GREEN}·{RESET} {ftype}: {YELLOW}{fvalue}{RESET}"
                              + (f"  {MAGENTA}[{src}]{RESET}" if src else ""))
                else:
                    print(f"    {CYAN}Facts{RESET}       → {YELLOW}(none){RESET}")
            except json.JSONDecodeError:
                print(f"  {RED}❌ Invalid JSON in entry {idx}: {line[:100]}{RESET}")
        
    except Exception as e:
        print(f"{RED}❌ Error reading WAL: {e}{RESET}")


def show_storage_summary():
    """Show overall storage summary"""
    print_header("Storage Summary")
    
    print(f"{BLUE}Memory Layer Locations:{RESET}\n")
    
    items = [
        ("ChromaDB", CHROMA_DB_PATH, "Vector embeddings of facts"),
        ("Mem0 SQLite", MEM0_HISTORY_PATH, "Metadata & relationships"),
        ("WAL (Write-Ahead Log)", WAL_PATH.parent, "Source of truth, each fact recorded"),
        ("Redis Cache", Path("/tmp/ps01_cache"), "Speed layer, TTL-based caching"),
    ]
    
    for name, path, desc in items:
        exists = "✅" if path.exists() else "❌"
        size = ""
        if path.exists() and path.is_file():
            size = f" ({path.stat().st_size / 1024:.1f}KB)"
        elif path.exists() and path.is_dir():
            total_size = sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
            size = f" ({total_size / 1024:.1f}KB total)"
        
        print(f"{exists} {MAGENTA}{name:20s}{RESET} → {CYAN}{str(path)}{size}{RESET}")
        print(f"   {YELLOW}{desc}{RESET}\n")


def show_usage():
    print(f"""
{BOLD}Memory Storage Inspector{RESET}

{YELLOW}Usage:{RESET}
  python3 scripts/inspect_memory_storage.py [--option]

{YELLOW}Options:{RESET}
  --chromadb    View ChromaDB vector store
  --sqlite      View Mem0 SQLite metadata database
  --wal         View Write-Ahead Log entries
  --all         View everything (default)
  --summary     Show storage locations only
  --help        Show this help message

{YELLOW}Examples:{RESET}
  python3 scripts/inspect_memory_storage.py --chromadb
  python3 scripts/inspect_memory_storage.py --sqlite
  python3 scripts/inspect_memory_storage.py --wal
  python3 scripts/inspect_memory_storage.py --all
""")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        option = sys.argv[1]
        
        if option == "--help":
            show_usage()
        elif option == "--chromadb":
            inspect_chromadb()
        elif option == "--sqlite":
            inspect_mem0_sqlite()
        elif option == "--wal":
            inspect_wal()
        elif option == "--summary":
            show_storage_summary()
        elif option == "--all":
            show_storage_summary()
            inspect_chromadb()
            inspect_mem0_sqlite()
            inspect_wal()
        else:
            print(f"{RED}Unknown option: {option}{RESET}")
            show_usage()
    else:
        # Default: show all
        show_storage_summary()
        inspect_chromadb()
        inspect_mem0_sqlite()
        inspect_wal()

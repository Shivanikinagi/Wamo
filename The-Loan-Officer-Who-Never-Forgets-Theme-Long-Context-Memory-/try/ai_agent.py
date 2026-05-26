import requests
import sqlite3

OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """
Tum ek professional loan officer ho — BrainBack Bank ke liye.
Hindi + English mix mein baat karo.
Short, helpful, and polite answers do.
"""


def ask_ollama(prompt: str) -> str:
    payload = {"model": "phi4-mini", "prompt": f"{SYSTEM_PROMPT}\n\nUser: {prompt}", "stream": False}
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    return response.json().get("response", "")


if __name__ == "__main__":
    print(ask_ollama("Customer ne home loan ke liye enquiry ki hai."))

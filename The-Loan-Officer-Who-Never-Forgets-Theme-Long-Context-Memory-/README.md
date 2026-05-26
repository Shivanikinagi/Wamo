# 🏦 The Loan Officer Who Never Forgets
### *BrainBack.AI — A Theme-Based Long-Context Memory System*

---

## 🏗️ Project Overview

BrainBack.AI is a voice terminal for loan officers. It combines a browser UI, FastAPI backend, and webhook-driven transcript capture to make customer conversations easier to follow across sessions.

## ✨ Key Features

- Real-time voice interface from the browser.
- Hinglish persona for Hindi + English conversations.
- Live transcription in the UI and terminal.
- Memory-oriented call workflow for repeat customer context.
- Glassmorphic terminal-style presentation.

## 🚀 Quick Start Guide

### Prerequisites

- Python 3.8+
- A Vapi.ai account for API keys.

### Installation

```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
VAPI_API_KEY=your_vapi_api_key
VAPI_PUBLIC_KEY=your_vapi_public_key
VAPI_ASSISTANT_ID=your_vapi_assistant_id
PORT=8000
```

### Launch

```bash
uvicorn app.main:app --reload --port 8000
```

## 📁 File Structure

- `app/main.py` - FastAPI entry point
- `app/static/` - Frontend assets
- `app/routes/` - Webhook handlers
- `try/` - Experimental local voice/LLM scripts

## 🛠️ Tech Stack

- FastAPI
- Vapi AI
- Vanilla JS / CSS
- Faster-Whisper experiment
- Ollama experiment

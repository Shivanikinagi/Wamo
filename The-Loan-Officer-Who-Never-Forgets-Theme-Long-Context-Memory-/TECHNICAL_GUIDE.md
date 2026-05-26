# 🛠️ Technical Implementation Guide: BrainBack.AI

This project is a small browser-driven voice demo with a FastAPI backend and webhook-based transcript logging.

## System Architecture

### 1. Frontend: The Voice Terminal
- Vanilla HTML5, CSS3, and JavaScript.
- Uses the Vapi web SDK to create the call experience.
- Displays call state and transcripts in the browser.

### 2. Backend: FastAPI Service
- Serves config via `/api/config`.
- Receives Vapi webhooks at `/api/vapi/webhook`.
- Prints transcript and call lifecycle events to the terminal.

### 3. Experimental Local Tools
- `try/transcribe.py` shows local ASR experimentation.
- `try/ai_agent.py` shows a local Ollama-based persona test.

## Data Flow

1. Browser loads the frontend and fetches config.
2. User starts a Vapi-powered call.
3. Transcript events are sent to the backend webhook.
4. The backend stores lightweight call state and prints it for debugging.

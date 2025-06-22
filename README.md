# ☎️ XDial

**XDial** is an AI-powered IVR (Interactive Voice Response) crawler that maps and navigates automated phone trees using Twilio, Whisper, and GPT. It saves and reuses each company’s phone tree so users never have to "Press 1" blindly again.

---

## 🚀 Features

- 🎙️ **Passive Listening + IVR Detection**
  - Uses Whisper to detect "menu", "open-ended", or "hybrid" IVR prompts
- 🧠 **AI-Driven Query Injection**
  - GPT-4o decides when to speak user queries or wait
- 🔁 **Recursive Phone Tree Crawler**
  - Automatically presses buttons and records every branch
- 🌲 **Tree Visualization Ready**
  - Each session builds a structured IVR tree saved to Firebase
- 🔐 **Secure Secret Management**
  - `.env` file is ignored by Git; secrets never committed
- 📼 **Audio Recording & Analysis**
  - Records all responses and timestamps prompts for smarter delay control

---

## 📦 Tech Stack

- **Backend**: FastAPI, Python, Whisper, OpenAI GPT-4o
- **Telephony**: Twilio (calls, DTMF, recordings)
- **Storage**: Firebase Realtime DB + local snapshots
- **Frontend**: Vue 3 (tree visualization in progress)

---

## 🔧 Setup

### 1. Clone the repo

```bash
git clone https://github.com/BryanDungan/XDial.git
cd XDial

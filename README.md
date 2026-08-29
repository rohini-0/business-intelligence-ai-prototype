# BusinessIntelligence.ai Prototype

A working demo that detects KPI movements, ranks their drivers with honest
confidence levels, and explains them differently for two personas -- built
from real Superstore, Telco, and Support Ticket data, reconciled at the
regional level without fabricating customer identity across sources.

See `semantic_contract.md` for full KPI definitions and data-source disclosures.

---

## How to run this yourself (step by step, no experience assumed)

### Part 1 -- Get a GitHub repo with this code in it

1. Go to https://github.com and log in (or create a free account).
2. Click the **+** icon top-right → **New repository**.
3. Name it something like `business-intelligence-ai-prototype`. Keep it **Public**
   (private repos can't connect to free hosting). Click **Create repository**.
4. On the new repo's page, click **uploading an existing file** (a blue link
   in the middle of the page).
5. Drag in ALL the files from this folder: `app.py`, `requirements.txt`,
   `semantic_contract.md`, `README.md`, and the whole `data` folder (with
   `final_kpi_dataset.csv` inside it).
6. Scroll down, click **Commit changes**. Done -- your code is now on GitHub.

### Part 2 -- Make it a live, clickable web app (free, ~2 minutes)

1. Go to https://share.streamlit.io and log in with your GitHub account.
2. Click **Create app** → **From existing repo**.
3. Pick the repo you just made. For "Main file path" type `app.py`.
4. Click **Deploy**. Wait ~1-2 minutes.
5. You now have a real URL (like `yourname-yourapp.streamlit.app`) that
   ANYONE can open and click through -- including judges. No installation
   needed on their end.

### Part 3 (optional but recommended) -- Add your API key for live narratives

Without an API key, the app still works and shows realistic example
narratives (clearly labeled "offline fallback"). To make it generate live,
real-time narratives instead:

1. Get an API key from https://console.anthropic.com (Anthropic's website).
2. On the live app, open the sidebar and paste your key into the "Anthropic
   API key" box. It's only used in your browser session -- it is not saved
   anywhere.

---

## How to run it on your own computer instead (if you want to test first)

1. Install Python if you don't have it: https://www.python.org/downloads
2. Open a terminal (Command Prompt on Windows, Terminal on Mac) in this folder.
3. Run: `pip install -r requirements.txt`
4. Run: `streamlit run app.py`
5. It opens automatically in your browser at `http://localhost:8501`.

---

## What's actually in this folder

| File | What it is |
|---|---|
| `app.py` | The whole working app |
| `data/final_kpi_dataset.csv` | The real, reconciled dataset (region x week) |
| `semantic_contract.md` | KPI definitions, formulas, and honest data-source disclosures |
| `requirements.txt` | The two Python packages needed (streamlit, pandas) |

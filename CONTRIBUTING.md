# Contributing

## Development Setup

```bash
git clone https://github.com/V0rt/pipeline-dashboard.git
cd pipeline-dashboard
pip install -r requirements.txt
python3 server.py
```

Open http://localhost:8800

## Project Structure

```
pipeline-dashboard/
├── server.py              # FastAPI backend + SSE
├── static/
│   └── index.html         # Single-page kanban dashboard
├── .github/workflows/
│   └── ci.yml             # GitHub Actions CI
├── requirements.txt
├── pyproject.toml
├── run.sh
├── README.md
├── SECURITY.md
└── LICENSE
```

## Code Style

- Python: ruff (PEP 8)
- HTML/CSS: no build step, pure vanilla
- Single file frontend, dark theme

## CI/CD

GitHub Actions runs on every push to main / PR:
- `lint` — ruff check
- `test` — import verification
- `build` — Python package build
- `static-analysis` — HTML/JS structure checks
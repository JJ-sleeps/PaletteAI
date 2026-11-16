"""Start PaletteAI:  python run.py  [--port 8000] [--host 127.0.0.1]"""
import argparse
import os

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the PaletteAI web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()
    print(f"PaletteAI: http://{args.host}:{args.port}   API docs: http://{args.host}:{args.port}/docs")
    uvicorn.run("paletteai.main:app", host=args.host, port=args.port)

"""
S.T.E.W — Hugging Face Spaces entry point.
HF Spaces runs this file with `python app.py` or via the Gradio SDK.
We just launch the FastAPI app via uvicorn on the HF-assigned port.
"""
import os
import uvicorn

# HF Spaces sets PORT env var; default 7860
PORT = int(os.environ.get("PORT", 7860))

if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,
        log_level="info",
    )

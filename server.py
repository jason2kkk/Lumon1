"""
server.py — FastAPI 入口

职责：CORS、静态文件 serve、路由挂载
启动：uvicorn server:app --reload --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api_routes import router

app = FastAPI(title="需求挖掘 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Serve React build output if it exists (production mode)
build_dir = Path(__file__).parent / "frontend" / "dist"
if build_dir.exists():
    app.mount("/", StaticFiles(directory=str(build_dir), html=True), name="frontend")

"""
S.T.E.W — Secret Task Execution Worker
Central Intelligence Brain v4.0 — OpenRouter Edition
======================================================
Smart multi-model routing:
  Groq         → Speed (chat, simple tasks)
  OpenRouter   → Power (deep research, code, vision, creative)
  Anthropic    → Premium fallback
  OpenAI       → Vision fallback
Created by Emmanuel Ene Rejoice Gideon — MUTYINT, Nigeria
"""

import asyncio
import base64
import json
import os
import re
import requests
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger


# ─────────────────────────────────────────
# OPENROUTER FREE MODELS — Best in Class
# ─────────────────────────────────────────
OPENROUTER_MODELS = {
    # General intelligence — most powerful free model
    "power":    "meta-llama/llama-3.1-405b-instruct:free",
    # Code generation — best free coder
    "code":     "qwen/qwen-2.5-coder-32b-instruct:free",
    # Deep research + long context
    "research": "deepseek/deepseek-r1:free",
    # Creative writing, docs, proposals
    "creative": "mistralai/mistral-7b-instruct:free",
    # Vision + image understanding
    "vision":   "meta-llama/llama-3.2-11b-vision-instruct:free",
    # Fast responses (small but smart)
    "fast":     "google/gemma-3-4b-it:free",
    # Fallback — always available
    "fallback": "mistralai/mistral-7b-instruct:free",
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class StewBrain:
    """
    The master intelligence of S.T.E.W v4.0
    Smart routing across Groq + OpenRouter + Anthropic + OpenAI
    """

    def __init__(self, soul, heart, memory_engine):
        self.soul = soul
        self.heart = heart
        self.memory = memory_engine
        self.active_tasks = {}
        self.completed_tasks = []
        self.skill_registry = {}
        self.api_keys = self._load_api_keys()
        self.llm = self._init_llm()
        self.vision_llm = self._init_vision_llm()
        self.openrouter_available = bool(self.api_keys.get("openrouter"))
        logger.info(f"🧠 S.T.E.W Brain v4.0 — ONLINE | OpenRouter: {'✅' if self.openrouter_available else '❌'}")

    def _load_api_keys(self):
        return {
            "groq":        os.getenv("GROQ_API_KEY", ""),
            "openrouter":  os.getenv("OPENROUTER_API_KEY", ""),
            "openai":      os.getenv("OPENAI_API_KEY", ""),
            "anthropic":   os.getenv("ANTHROPIC_API_KEY", ""),
            "serper":      os.getenv("SERPER_API_KEY", "1867df2e3c379ba68185b39b64f2b986fba9e78e"),
            "huggingface": os.getenv("HF_TOKEN", ""),
            "ocr_space":   os.getenv("OCR_SPACE_KEY", ""),
        }

    def _init_llm(self):
        """Initialize primary LLM — Groq first (speed), OpenRouter ready for power tasks"""
        # Groq — fastest for simple tasks
        try:
            from groq import Groq
            if self.api_keys.get("groq"):
                client = Groq(api_key=self.api_keys["groq"])
                logger.info("🤖 Primary Brain: Groq LLaMA-3.3-70B — ONLINE")
                return {"provider": "groq", "client": client, "model": "llama-3.3-70b-versatile"}
        except Exception as e:
            logger.warning(f"Groq init failed: {e}")

        # OpenRouter as primary if no Groq
        if self.api_keys.get("openrouter"):
            logger.info("🤖 Primary Brain: OpenRouter Llama-405B — ONLINE")
            return {"provider": "openrouter", "client": None, "model": OPENROUTER_MODELS["power"]}

        # Anthropic fallback
        try:
            import anthropic
            if self.api_keys.get("anthropic"):
                client = anthropic.Anthropic(api_key=self.api_keys["anthropic"])
                logger.info("🤖 Brain: Anthropic Claude — ONLINE")
                return {"provider": "anthropic", "client": client, "model": "claude-3-5-sonnet-20241022"}
        except Exception as e:
            logger.warning(f"Anthropic init failed: {e}")

        # OpenAI fallback
        try:
            from openai import OpenAI
            if self.api_keys.get("openai"):
                client = OpenAI(api_key=self.api_keys["openai"])
                logger.info("🤖 Brain: OpenAI GPT-4o — ONLINE")
                return {"provider": "openai", "client": client, "model": "gpt-4o"}
        except Exception as e:
            logger.warning(f"OpenAI init failed: {e}")

        logger.warning("⚠️ No LLM key found — limited mode")
        return {"provider": "fallback", "client": None, "model": "stew-internal"}

    def _init_vision_llm(self):
        """Initialize vision model — OpenRouter vision → Groq vision → OpenAI → Tesseract"""
        # OpenRouter free vision model
        if self.api_keys.get("openrouter"):
            logger.info("👁️ Vision: OpenRouter LLaMA-3.2-Vision — ONLINE")
            return {"provider": "openrouter_vision", "client": None, "model": OPENROUTER_MODELS["vision"]}

        # OpenAI GPT-4o vision
        try:
            from openai import OpenAI
            if self.api_keys.get("openai"):
                client = OpenAI(api_key=self.api_keys["openai"])
                logger.info("👁️ Vision: OpenAI GPT-4o Vision — ONLINE")
                return {"provider": "openai", "client": client, "model": "gpt-4o"}
        except Exception:
            pass

        # Groq vision
        try:
            from groq import Groq
            if self.api_keys.get("groq"):
                client = Groq(api_key=self.api_keys["groq"])
                logger.info("👁️ Vision: Groq LLaMA Vision — ONLINE")
                return {"provider": "groq_vision", "client": client, "model": "llama-3.2-90b-vision-preview"}
        except Exception:
            pass

        logger.info("👁️ Vision: Tesseract OCR fallback — ONLINE")
        return {"provider": "tesseract", "client": None, "model": "tesseract-ocr"}

    def _task_to_model(self, task_type: str) -> str:
        """Route task type to best OpenRouter model"""
        mapping = {
            "code":     OPENROUTER_MODELS["code"],
            "research": OPENROUTER_MODELS["research"],
            "creative": OPENROUTER_MODELS["creative"],
            "vision":   OPENROUTER_MODELS["vision"],
            "fast":     OPENROUTER_MODELS["fast"],
            "power":    OPENROUTER_MODELS["power"],
        }
        return mapping.get(task_type, OPENROUTER_MODELS["fallback"])

    def _choose_mode(self, task: str) -> str:
        """Smart task routing — S.T.E.W decides best approach"""
        t = task.lower()
        if any(w in t for w in ["browse", "visit", "click", "navigate", "open website", "go to", "login", "fill form", "screenshot"]):
            return "browse"
        if any(w in t for w in ["research", "deep dive", "investigate", "analyse thoroughly", "find everything"]):
            return "research"
        if any(w in t for w in ["search", "find", "look up", "what is", "who is", "latest news", "current"]):
            return "search"
        if any(w in t for w in ["write code", "build app", "create script", "program", "function", "debug", "code"]):
            return "code"
        if any(w in t for w in ["create pdf", "make document", "write report", "build spreadsheet", "make slide"]):
            return "build"
        if any(w in t for w in ["image", "photo", "picture", "what do you see", "describe this", "read this image", "ocr"]):
            return "vision"
        if any(w in t for w in ["translate", "in french", "in spanish", "in igbo", "in yoruba", "in hausa"]):
            return "translate"
        if any(w in t for w in ["weather", "temperature", "forecast"]):
            return "weather"
        if any(w in t for w in ["stock", "price of", "crypto", "bitcoin", "market"]):
            return "finance"
        if any(w in t for w in ["currency", "convert", "usd to", "ngn to", "dollar to naira"]):
            return "currency"
        if any(w in t for w in ["write", "create", "generate", "essay", "story", "letter", "email", "poem"]):
            return "creative"
        return "respond"

    # ─────────────────────────────────────────
    # OPENROUTER CALL — Core Power Engine
    # ─────────────────────────────────────────
    def _call_openrouter(self, prompt: str, system: str, model: str = None, max_tokens: int = 4096) -> str:
        """Direct OpenRouter API call — supports all 200+ models"""
        if not self.api_keys.get("openrouter"):
            raise Exception("No OpenRouter API key")

        model = model or OPENROUTER_MODELS["power"]
        headers = {
            "Authorization": f"Bearer {self.api_keys['openrouter']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://stew-agent.onrender.com",
            "X-Title": "S.T.E.W — Secret Task Execution Worker",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_openrouter_vision(self, image_source: str, question: str) -> str:
        """OpenRouter vision call with image URL or base64"""
        if not self.api_keys.get("openrouter"):
            raise Exception("No OpenRouter API key")

        if image_source.startswith("http"):
            image_content = {"type": "image_url", "image_url": {"url": image_source}}
        else:
            image_content = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_source}"}}

        headers = {
            "Authorization": f"Bearer {self.api_keys['openrouter']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://stew-agent.onrender.com",
            "X-Title": "S.T.E.W Vision",
        }
        payload = {
            "model": OPENROUTER_MODELS["vision"],
            "messages": [{
                "role": "user",
                "content": [image_content, {"type": "text", "text": question}]
            }],
            "max_tokens": 1024,
        }
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=45
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ─────────────────────────────────────────
    # MAIN LLM CALL — Smart Routing
    # ─────────────────────────────────────────
    async def call_llm(self, prompt: str, system: str = None, max_tokens: int = 4096,
                       task_type: str = "respond") -> str:
        """
        Smart LLM call with automatic routing:
        - Fast/simple → Groq
        - Code → OpenRouter Qwen Coder
        - Research → OpenRouter DeepSeek R1
        - Creative → OpenRouter Mistral
        - Power → OpenRouter Llama 405B
        - Fallback chain if any fails
        """
        if not system:
            system = (
                "You are S.T.E.W — Secret Task Execution Worker. "
                "An elite autonomous AI agent created by Emmanuel Ene Rejoice Gideon of MUTYINT, Nigeria. "
                "You coordinate 100 specialized AI agents to complete any task. "
                "You are more powerful than Manus, Kimi, and Hermes combined. "
                "Think deeply, act precisely, and always deliver excellent structured results."
            )

        # Route to best model based on task type
        use_openrouter_for = {"code", "research", "creative", "power", "vision"}

        # For speed tasks — use Groq if available
        if task_type not in use_openrouter_for and self.llm["provider"] == "groq":
            try:
                response = self.llm["client"].chat.completions.create(
                    model=self.llm["model"],
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                logger.info(f"🟢 Groq responded [{task_type}]")
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"Groq failed, falling back to OpenRouter: {e}")

        # Use OpenRouter for power tasks OR Groq fallback
        if self.openrouter_available:
            try:
                or_model = self._task_to_model(task_type)
                result = self._call_openrouter(prompt, system, model=or_model, max_tokens=max_tokens)
                logger.info(f"🔀 OpenRouter [{or_model.split('/')[-1]}] responded [{task_type}]")
                return result
            except Exception as e:
                logger.warning(f"OpenRouter failed: {e}")

        # Groq fallback (if OpenRouter failed)
        if self.llm["provider"] == "groq":
            try:
                response = self.llm["client"].chat.completions.create(
                    model=self.llm["model"],
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.error(f"Groq fallback failed: {e}")

        # Anthropic fallback
        try:
            if self.llm["provider"] == "anthropic":
                response = self.llm["client"].messages.create(
                    model=self.llm["model"],
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic fallback failed: {e}")

        # OpenAI last resort
        try:
            if self.llm["provider"] == "openai":
                response = self.llm["client"].chat.completions.create(
                    model=self.llm["model"],
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI fallback failed: {e}")

        return "S.T.E.W needs an API key to provide responses. Please set GROQ_API_KEY or OPENROUTER_API_KEY."

    # ─────────────────────────────────────────
    # VISION ANALYSIS
    # ─────────────────────────────────────────
    async def analyze_image(self, image_source: str, question: str = "Describe everything in detail") -> str:
        """Analyze image — OpenRouter Vision → Groq Vision → OpenAI → Tesseract OCR"""
        logger.info(f"👁️ Vision: {question[:60]}")

        # OpenRouter vision (free)
        if self.openrouter_available:
            try:
                result = self._call_openrouter_vision(image_source, question)
                logger.info("👁️ OpenRouter Vision responded")
                return result
            except Exception as e:
                logger.warning(f"OpenRouter vision failed: {e}")

        # Groq vision
        try:
            if self.vision_llm["provider"] == "groq_vision":
                if image_source.startswith("http"):
                    image_content = {"type": "image_url", "image_url": {"url": image_source}}
                else:
                    image_content = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_source}"}}
                response = self.vision_llm["client"].chat.completions.create(
                    model=self.vision_llm["model"],
                    messages=[{"role": "user", "content": [image_content, {"type": "text", "text": question}]}],
                    max_tokens=1024
                )
                return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Groq vision failed: {e}")

        # OpenAI vision
        try:
            if self.vision_llm["provider"] == "openai":
                if image_source.startswith("http"):
                    image_content = {"type": "image_url", "image_url": {"url": image_source}}
                else:
                    image_content = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_source}"}}
                response = self.vision_llm["client"].chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": [image_content, {"type": "text", "text": question}]}],
                    max_tokens=1024
                )
                return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"OpenAI vision failed: {e}")

        # Tesseract OCR last resort
        try:
            import pytesseract
            from PIL import Image
            import io
            if image_source.startswith("http"):
                resp = requests.get(image_source, timeout=10)
                img = Image.open(io.BytesIO(resp.content))
            else:
                img = Image.open(io.BytesIO(base64.b64decode(image_source)))
            text = pytesseract.image_to_string(img)
            return f"OCR Text extracted:\n{text}" if text.strip() else "No text found in image"
        except Exception as e:
            return f"Vision analysis failed: {str(e)}"

    # ─────────────────────────────────────────
    # MODELS INFO — what's available
    # ─────────────────────────────────────────
    def get_models_info(self) -> Dict:
        """Return info about all active models"""
        return {
            "primary_llm": self.llm["provider"],
            "primary_model": self.llm["model"],
            "openrouter": self.openrouter_available,
            "openrouter_models": OPENROUTER_MODELS if self.openrouter_available else {},
            "vision": self.vision_llm["provider"],
            "routing": {
                "fast_tasks": "Groq LLaMA-3.3-70B",
                "code_tasks": "OpenRouter Qwen-2.5-Coder-32B" if self.openrouter_available else "Groq",
                "research_tasks": "OpenRouter DeepSeek-R1" if self.openrouter_available else "Groq",
                "creative_tasks": "OpenRouter Mistral-7B" if self.openrouter_available else "Groq",
                "power_tasks": "OpenRouter Llama-3.1-405B" if self.openrouter_available else "Groq",
                "vision_tasks": "OpenRouter LLaMA-3.2-Vision" if self.openrouter_available else self.vision_llm["provider"],
            }
        }

    # ─────────────────────────────────────────
    # THINK — main task handler
    # ─────────────────────────────────────────
    async def _fetch_web_context(self, query: str) -> str:
        """Fetch real-time web results — tries env key first, falls back to hardcoded"""
        try:
            import httpx
            from datetime import datetime
            # Try env key first, then hardcoded fallback
            serper_key = (
                os.getenv("SERPER_API_KEY", "").strip()
                or "1867df2e3c379ba68185b39b64f2b986fba9e78e"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": 6, "gl": "ng", "hl": "en"}
                )
                if r.status_code == 200:
                    data = r.json()
                    snippets = []
                    # Answer box (instant answer)
                    ab = data.get("answerBox", {})
                    if ab.get("answer") or ab.get("snippet"):
                        snippets.append(f"⚡ DIRECT ANSWER: {ab.get('answer') or ab.get('snippet','')}")
                    # Knowledge graph
                    kg = data.get("knowledgeGraph", {})
                    if kg.get("description"):
                        snippets.append(f"📚 KNOWLEDGE: {kg.get('title','')} — {kg.get('description','')[:200]}")
                    # Organic results
                    for item in data.get("organic", [])[:5]:
                        date_str = f" [{item.get('date','')}]" if item.get("date") else ""
                        snippets.append(f"• {item.get('title','')}{date_str} — {item.get('snippet','')}")
                    if snippets:
                        header = f"\n\n[REAL-TIME WEB DATA — Retrieved {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}]:\n"
                        return header + "\n".join(snippets) + "\n[END REAL-TIME DATA]"
                elif r.status_code == 403:
                    logger.warning("Serper key invalid/expired")
        except Exception as e:
            logger.warning(f"Web context fetch failed: {e}")
        return ""

    async def think(self, task: str, context: Dict = None) -> Dict:
        """Master task handler — routes to right agent and model"""
        mode = self._choose_mode(task)
        logger.info(f"🧠 Task mode: {mode} | Task: {task[:60]}")

        # Map mode to OpenRouter task type
        mode_to_type = {
            "code":     "code",
            "research": "research",
            "creative": "creative",
            "browse":   "power",
            "vision":   "vision",
            "respond":  "respond",
        }
        task_type = mode_to_type.get(mode, "respond")

        # 🌐 AUTO WEB GROUNDING — inject real-time data for research/news queries
        web_context = ""
        research_keywords = [
            "news", "latest", "today", "current", "recent", "2025", "2026", "now",
            "price", "stock", "weather", "crypto", "bitcoin", "market", "rate",
            "what happened", "who won", "who is", "where is", "when did",
            "how much", "how many", "exchange rate", "naira", "dollar",
            "breaking", "update", "announce", "release", "launch"
        ]
        should_search = any(kw in task.lower() for kw in research_keywords)
        if should_search:
            web_context = await self._fetch_web_context(task)

        system = (
            f"You are S.T.E.W — Secret Task Execution Worker, an elite autonomous AI agent created by Emmanuel Ene Rejoice Gideon of MUTYINT Nigeria. "
            f"You are operating in [{mode.upper()}] mode. Current time: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}. "
            f"CRITICAL RULES — you MUST follow these without exception:\n"
            f"1. You have LIVE internet access via Serper API. NEVER say 'as of my knowledge cutoff' or 'I cannot access real-time data'.\n"
            f"2. When web search results are provided below, treat them as GROUND TRUTH. Cite sources.\n"
            f"3. When no web results are provided, state the date and note you are answering from training knowledge.\n"
            f"4. For finance/weather/news: ALWAYS use the web search results. Never guess prices.\n"
            f"5. You coordinate 100 specialized agents. Be thorough, structured, and deliver excellence."
        )

        full_task = task + web_context if web_context else task
        response = await self.call_llm(full_task, system=system, task_type=task_type)

        return {
            "task": task,
            "mode": mode,
            "model_used": self.llm["model"],
            "openrouter_used": self.openrouter_available and task_type in {"code", "research", "creative", "power"},
            "web_grounded": bool(web_context),
            "output": response,
            "timestamp": datetime.now().isoformat(),
        }

    # ─────────────────────────────────────────
    # MULTI-AGENT — 100 agents on one task
    # ─────────────────────────────────────────
    async def deploy_all_agents(self, task: str) -> Dict:
        """Deploy all 100 agents on a single task — maximum power mode"""
        logger.info(f"🚀 100-Agent Deploy: {task[:60]}")

        # Break task into sub-tasks for parallel agent processing
        decompose_prompt = f"""
Break this task into 5 parallel sub-tasks for 5 specialist agents:
Task: {task}

Return exactly 5 sub-tasks, one per line, each prefixed with AGENT_N: where N is 1-5.
"""
        decomposition = await self.call_llm(decompose_prompt, task_type="power")

        # Extract sub-tasks
        sub_tasks = []
        for line in decomposition.split("\n"):
            if line.strip().startswith("AGENT_"):
                sub_tasks.append(line.split(":", 1)[-1].strip())

        if not sub_tasks:
            sub_tasks = [task]

        # Run sub-tasks in parallel
        async def run_sub_task(st, idx):
            sys_prompt = (
                f"You are Agent #{idx+1} of S.T.E.W's 100-agent system. "
                f"Focus ONLY on your assigned sub-task. Be comprehensive and specific."
            )
            return await self.call_llm(st, system=sys_prompt, task_type="power")

        results = await asyncio.gather(*[run_sub_task(st, i) for i, st in enumerate(sub_tasks)])

        # Synthesize all results
        synthesis_prompt = f"""
You are S.T.E.W MasterBrainAgent. Synthesize these 5 agent reports into one unified, comprehensive answer.

Original Task: {task}

{chr(10).join([f"Agent {i+1} Report:{chr(10)}{r}" for i, r in enumerate(results)])}

Provide a clear, well-structured synthesis. Use headers. Be comprehensive.
"""
        synthesis = await self.call_llm(synthesis_prompt, task_type="power", max_tokens=8192)

        return {
            "task": task,
            "agents_deployed": len(sub_tasks),
            "sub_tasks": sub_tasks,
            "synthesis": synthesis,
            "timestamp": datetime.now().isoformat(),
        }

    # ─────────────────────────────────────────
    # LEGACY TASK HANDLER
    # ─────────────────────────────────────────
    async def execute_task(self, task: str, context=None, skills=None) -> Dict:
        """Legacy task entry point — forwards to think()"""
        return await self.think(task, context)

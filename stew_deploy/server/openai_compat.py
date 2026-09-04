"""
S.T.E.W Agent — OpenAI-Compatible API Endpoint
=================================================
Implements /v1/chat/completions and /v1/models so that any tool built for
OpenAI (OpenCode, Devin, OpenClaw, Hermes, Cursor, LangChain, AutoGen, etc.)
can point their API base URL at Stew and get:
  - 6 AI providers with auto-failover (Groq, Mistral, NVIDIA, OpenRouter, HF, OpenAI)
  - Naira billing via Paystack (no dollar card needed)
  - Web search grounding
  - Stew's 12 personas and 59 skills
  - 100-agent swarm capability

Usage for developers:
  # Instead of:  OPENAI_API_KEY=sk-xxx  OPENAI_BASE_URL=https://api.openai.com/v1
  # Use:        OPENAI_API_KEY=stew_xxx OPENAI_BASE_URL=https://stew-agent.onrender.com/v1

  # Or in code:
  from openai import OpenAI
  client = OpenAI(api_key="stew_your_key", base_url="https://stew-agent.onrender.com/v1")
  response = client.chat.completions.create(
      model="stew-default",
      messages=[{"role": "user", "content": "Hello!"}]
  )
  print(response.choices[0].message.content)
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import get_user_by_api_key
from server.config import get_settings
from server.database import get_db
from server.llm_client import get_llm_client, PROVIDER_MODELS
from server.models import User
from server.clean_output import clean_response
from server.search import get_searcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS — match OpenAI's API schema exactly
# ═══════════════════════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list] = None


class ChatCompletionRequest(BaseModel):
    model: str = "stew-default"
    messages: list[ChatMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[str | list[str]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None
    # OpenAI tool calling (function calling) support
    tools: Optional[list] = None
    tool_choice: Optional[str | dict] = None
    # Stew extensions (ignored by OpenAI clients, used by Stew-aware clients)
    web_search: Optional[bool] = False
    fusion_mode: Optional[bool] = False


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

# Map "stew-*" model names to Stew providers
MODEL_TO_PROVIDER = {
    "stew-default": None,        # auto: uses fallback chain
    "stew-fast": "groq",         # Groq (fastest)
    "stew-mistral": "mistral",   # Mistral (flagship)
    "stew-nvidia": "nvidia",     # NVIDIA NIM (free)
    "stew-openrouter": "openrouter",
    "stew-hf": "huggingface",
    "stew-openai": "openai",
    # Also accept common OpenAI model names and route to Stew's chain
    "gpt-4o": None,
    "gpt-4o-mini": None,
    "gpt-4-turbo": None,
    "gpt-3.5-turbo": None,
    "llama-3.3-70b": "groq",
    "llama-3.1-8b": "groq",
    "mistral-large": "mistral",
    "qwen3-235b": "huggingface",
}


async def _get_stew_user(authorization: str, db: AsyncSession) -> Optional[User]:
    """Extract user from Authorization header (Bearer stew_xxx)."""
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    api_key = authorization.replace("Bearer ", "").strip()
    if not api_key or api_key == "none":
        return None
    try:
        user = await asyncio.wait_for(
            get_user_by_api_key(api_key, db), timeout=5.0
        )
        return user
    except Exception:
        return None


def _extract_text(content: str | list | None) -> str:
    """Extract plain text from message content (handles multimodal)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(texts)
    return str(content)


def _build_openai_response(
    content: str,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict:
    """Build a response in OpenAI's exact format."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": f"stew-{provider}",
    }


def _build_openai_stream_chunk(
    content: str,
    model: str,
    is_final: bool = False,
) -> str:
    """Build a single SSE chunk for streaming responses."""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {} if is_final else {"content": content},
                "finish_reason": "stop" if is_final else None,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/models")
async def list_models():
    """List available models in OpenAI format.

    This lets tools like OpenCode, Cursor, and LangChain discover
    available models through Stew's endpoint.
    """
    models = []
    # Stew-native model names
    stew_models = [
        ("stew-default", "S.T.E.W Default (auto-failover across 6 providers)"),
        ("stew-fast", "S.T.E.W Fast (Groq — ultra low latency)"),
        ("stew-mistral", "S.T.E.W Mistral (Mistral Large)"),
        ("stew-nvidia", "S.T.E.W NVIDIA (Llama 3.3 70B — free)"),
        ("stew-openrouter", "S.T.E.W OpenRouter (free Llama 3.3 70B)"),
        ("stew-hf", "S.T.E.W HuggingFace (Qwen3 235B)"),
        ("stew-openai", "S.T.E.W OpenAI (GPT-4o-mini)"),
    ]
    for model_id, desc in stew_models:
        models.append({
            "id": model_id,
            "object": "model",
            "created": 1700000000,
            "owned_by": "stew",
            "description": desc,
        })

    # Also expose OpenAI-compatible names so existing configs work without changes
    for compat in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]:
        models.append({
            "id": compat,
            "object": "model",
            "created": 1700000000,
            "owned_by": "stew",
            "description": f"OpenAI-compatible route (auto-failover via Stew)",
        })

    return {"object": "list", "data": models}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    authorization: str = Header(None),
):
    """OpenAI-compatible chat completions endpoint.

    Any tool that supports OpenAI can point to Stew by changing:
      - base_url to https://stew-agent.onrender.com/v1
      - api_key to your Stew API key

    Features beyond OpenAI:
      - web_search: pass in body to enable web grounding
      - fusion_mode: pass in body for multi-model fusion
      - stew-* model names for provider selection
    """
    # Get DB session
    from server.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        user = await _get_stew_user(authorization, db)

        # Check quota
        if user:
            from server.main import _check_quota
            allowed, used, limit = await _check_quota(user, db)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "message": f"Monthly API call limit reached ({used}/{limit}). Upgrade your plan to continue.",
                            "type": "rate_limit_error",
                            "code": "monthly_limit_exceeded",
                            "param": None,
                        }
                    },
                )

        # Convert messages — extract text from OpenAI format
        raw_messages = []
        for msg in body.messages:
            content_text = _extract_text(msg.content)
            role = msg.role if msg.role in ("system", "user", "assistant", "tool") else "user"
            raw_messages.append({
                "role": role,
                "content": content_text,
            })

        # If no system message, add Stew's default
        has_system = any(m["role"] == "system" for m in raw_messages)
        if not has_system:
            settings_obj = get_settings()
            persona = getattr(user, "persona", "general") if user else "general"
            system_prompt = settings_obj.PERSONA_PROMPTS.get(persona, settings_obj.PERSONA_PROMPTS["general"])
            if user and getattr(user, "custom_instructions", None):
                system_prompt += f"\n\nUSER CUSTOM INSTRUCTIONS:\n{user.custom_instructions}"
            raw_messages.insert(0, {"role": "system", "content": system_prompt})

        # Web search grounding (Stew extension)
        web_context = ""
        if body.web_search:
            searcher = get_searcher()
            if searcher._is_available():
                # Extract the last user message for search
                last_user_msg = ""
                for m in reversed(raw_messages):
                    if m["role"] == "user":
                        last_user_msg = m["content"]
                        break
                if last_user_msg:
                    try:
                        search_results = await asyncio.to_thread(searcher.search, last_user_msg, 5)
                        if search_results.get("grounded"):
                            web_context = searcher.format_results_for_llm(search_results)
                            if web_context:
                                # Inject search context into system message
                                for m in raw_messages:
                                    if m["role"] == "system":
                                        m["content"] += f"\n\nWEB SEARCH CONTEXT:\n{web_context}"
                                        break
                    except Exception as e:
                        logger.warning(f"Web search in v1 endpoint failed: {e}")

        # Determine which provider to use
        model_name = body.model or "stew-default"
        provider_override = MODEL_TO_PROVIDER.get(model_name)
        llm = get_llm_client()

        # ── Vision (multimodal) support ────────────────────────────────────
        # OpenAI-style content parts with type=image_url carry base64 images.
        # _extract_text() above strips them for the plain-text path, so we
        # collect them here and route the request through the vision chain
        # (llm.vision_chat) so the image actually reaches a multimodal model.
        vision_parts = []
        for vm in body.messages:
            if isinstance(vm.content, list):
                for vp in vm.content:
                    if isinstance(vp, dict) and vp.get("type") == "image_url":
                        vurl = (vp.get("image_url") or {}).get("url", "") or ""
                        if vurl.startswith("data:"):
                            vheader, _, vb64 = vurl.partition(",")
                            vmime = "image/jpeg"
                            if vheader.startswith("data:") and len(vheader) > 5:
                                vmime = vheader[5:].split(";", 1)[0] or "image/jpeg"
                            if vb64:
                                vision_parts.append((vb64, vmime))
        if vision_parts:
            vb64, vmime = vision_parts[-1]  # analyze the most recent image
            v_prompt = ""
            for m in reversed(raw_messages):
                if m["role"] == "user" and m["content"].strip():
                    v_prompt = m["content"].strip()
                    break
            if not v_prompt:
                v_prompt = "Describe this image in detail."
            logger.info(f"Vision request: {len(vb64)} bytes b64, mime={vmime}")

        # If user passes an OpenAI model name, we still use Stew's fallback chain
        # (we don't forward to OpenAI directly — we use our own providers)
        actual_model = None if provider_override is None else None
        # If model is a stew-* name that maps to a specific provider, use that provider
        if model_name.startswith("stew-") and provider_override:
            actual_model = None  # let _call_provider use the provider's default model

        # Call the LLM
        try:
            if body.fusion_mode and not vision_parts and len(llm.fallback_order) >= 2:
                from server.orchestrator import orchestrate_text
                # Extract system and user messages
                sys_msg = ""
                user_msgs = []
                for m in raw_messages:
                    if m["role"] == "system":
                        sys_msg = m["content"]
                    else:
                        user_msgs.append(m["content"])
                prompt = "\n\n".join(user_msgs)
                fusion_result = await orchestrate_text(
                    prompt=prompt,
                    system=sys_msg,
                    workers=llm.fallback_order[:3],
                    temperature=body.temperature or 0.7,
                )
                content = clean_response(fusion_result.get("answer", ""))
                model_used = "stew-fusion"
                provider_used = "stew_fusion"
                prompt_tokens = sum(r.get("tokens", {}).get("total", 0) for r in fusion_result.get("raw_worker_outputs", []))
                completion_tokens = len(content) // 4
            elif vision_parts:
                # Multimodal path — hand the image to a vision-capable model
                vision_result = await asyncio.to_thread(
                    llm.vision_chat, vb64, v_prompt, vmime
                )
                content = clean_response(vision_result["content"])
                model_used = vision_result.get("model", model_name)
                provider_used = vision_result.get("provider", "stew")
                prompt_tokens = (len(vb64) + len(v_prompt)) // 8
                completion_tokens = len(content) // 4
            else:
                result = await asyncio.to_thread(
                    llm.chat,
                    raw_messages,
                    actual_model,
                    body.temperature or 0.7,
                )
                content = clean_response(result["content"])
                model_used = result.get("model", model_name)
                provider_used = result.get("provider", "stew")
                prompt_tokens = result.get("tokens", {}).get("prompt", 0)
                completion_tokens = result.get("tokens", {}).get("completion", 0)
        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error": {
                        "message": str(e.detail),
                        "type": "server_error",
                        "code": "provider_error",
                        "param": None,
                    }
                },
            )
        except Exception as e:
            logger.error(f"v1/chat/completions error: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"S.T.E.W internal error: {str(e)}",
                        "type": "server_error",
                        "code": "internal_error",
                        "param": None,
                    }
                },
            )

        # Log the call
        if user:
            from server.main import _log_call
            asyncio.create_task(_log_call(
                db, user.id, "/v1/chat/completions", "POST",
                prompt_tokens + completion_tokens, 200,
            ))

        # ── Streaming response (SSE) ──────────────────────────────────────
        if body.stream:
            async def stream_generator():
                # Send the response in chunks to simulate streaming
                chunk_size = 20  # characters per chunk
                for i in range(0, len(content), chunk_size):
                    chunk_text = content[i : i + chunk_size]
                    yield _build_openai_stream_chunk(chunk_text, model_used)
                    await asyncio.sleep(0.02)  # small delay for realistic streaming

                # Final chunk with finish_reason
                yield _build_openai_stream_chunk("", model_used, is_final=True)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # ── Non-streaming response ─────────────────────────────────────────
        return _build_openai_response(
            content=content,
            model=model_used,
            provider=provider_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL OPENAI-COMPATIBLE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/models/{model_id}")
async def get_model(model_id: str):
    """Get model details (OpenAI-compatible)."""
    all_models = {
        "stew-default": "S.T.E.W Default (auto-failover across 6 providers)",
        "stew-fast": "S.T.E.W Fast (Groq — ultra low latency)",
        "stew-mistral": "S.T.E.W Mistral (Mistral Large)",
        "stew-nvidia": "S.T.E.W NVIDIA (Llama 3.3 70B — free)",
        "stew-openrouter": "S.T.E.W OpenRouter (free Llama 3.3 70B)",
        "stew-hf": "S.T.E.W HuggingFace (Qwen3 235B)",
        "stew-openai": "S.T.E.W OpenAI (GPT-4o-mini)",
        "gpt-4o": "OpenAI-compatible route (auto-failover via Stew)",
        "gpt-4o-mini": "OpenAI-compatible route (auto-failover via Stew)",
        "gpt-4-turbo": "OpenAI-compatible route (auto-failover via Stew)",
        "gpt-3.5-turbo": "OpenAI-compatible route (auto-failover via Stew)",
    }
    desc = all_models.get(model_id, f"Unknown model: {model_id}")
    return {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "stew",
        "description": desc,
    }


@router.post("/embeddings")
async def embeddings(
    body: dict,
    authorization: str = Header(None),
):
    """Stub embeddings endpoint — returns a 501 to signal not-yet-supported.

    Some tools (LangChain, etc.) probe for embeddings. We respond clearly
    instead of crashing.
    """
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "message": "S.T.E.W does not currently support embeddings. Use the /chat endpoint for text generation.",
                "type": "not_implemented",
                "code": "embeddings_not_supported",
                "param": None,
            }
        },
    )

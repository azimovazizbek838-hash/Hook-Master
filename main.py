"""
HookMaster AI - FastAPI backend (5x AI Fallback: OpenAI, Groq, Gemini, Mistral, Cohere)
"""

import json
import logging
import os
import random
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator
import httpx

# Google GenAI import (Gemini uchun)
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hookmaster")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
COHERE_MODEL = os.getenv("COHERE_MODEL", "command-r-plus-08-2024")

RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

TONE_GUIDES: Dict[str, str] = {
    "viral": "Highly shareable, curiosity-gap driven, pattern-interrupting, scroll-stopping.",
    "funny": "Playful, relatable humor, memes, exaggerated reactions, light self-roasting.",
    "educational": "Clear, authoritative, value-packed, numbered tips or frameworks.",
    "controversial": "Bold hot takes that spark debate, challenge common beliefs (never hateful or harmful).",
    "energy": "Fast, loud, hype-driven, exclamation-heavy, urgent and motivating.",
}
TONE_ALIASES = {"high-energy": "energy", "high energy": "energy", "highenergy": "energy"}

CREATIVE_ANGLES: List[str] = [
    "Focus on a contrarian angle: challenge a common myth or popular advice in this niche.",
    "Focus on a specific, relatable mistake almost everyone makes without realizing it.",
    "Focus on a 'hidden secret' or insider shortcut that saves time or money immediately.",
    "Focus on a dramatic 'Before vs. After' transformation or unexpected personal story.",
    "Focus on high urgency and FOMO (Fear Of Missing Out) — why acting right now matters.",
    "Focus on a shocking statistic, counter-intuitive fact, or bold experiment result.",
    "Focus on a step-by-step micro-framework that feels effortless to apply today.",
    "Focus on calling out a specific type of viewer directly ('Stop scrolling if you...').",
]

# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=100)
    tone: str = Field("viral")

    @field_validator("topic")
    @classmethod
    def clean_topic(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Topic is too short.")
        return v

    @field_validator("tone")
    @classmethod
    def clean_tone(cls, v: str) -> str:
        key = v.strip().lower()
        key = TONE_ALIASES.get(key, key)
        if key not in TONE_GUIDES:
            raise ValueError(f"Tone must be one of: {', '.join(TONE_GUIDES)}")
        return key

class ScriptSegment(BaseModel):
    time: str
    visual: str
    audio: str

class GeneratedContent(BaseModel):
    hooks: List[str]
    script: List[ScriptSegment]
    cta: str
    cta_why: str
    caption: str
    hashtags: List[str]

    @field_validator("hooks")
    @classmethod
    def exactly_three_hooks(cls, v: List[str]) -> List[str]:
        v = [h.strip() for h in v if h and h.strip()]
        if len(v) < 3:
            raise ValueError("Need 3 hooks")
        return v[:3]

    @field_validator("script")
    @classmethod
    def script_not_empty(cls, v: List[ScriptSegment]) -> List[ScriptSegment]:
        if len(v) < 3:
            raise ValueError("Script too short")
        return v[:6]

    @field_validator("hashtags")
    @classmethod
    def clean_hashtags(cls, v: List[str]) -> List[str]:
        tags = []
        for t in v:
            t = t.strip().replace(" ", "")
            if not t:
                continue
            tags.append(t if t.startswith("#") else f"#{t}")
        return tags[:10]

class GenerateResponse(GeneratedContent):
    topic: str
    tone: str
    model: str

# --------------------------------------------------------------------------
# Prompt & Helpers
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an elite, unorthodox viral content strategist for top TikTok and Reels creators. 
CRITICAL RULE: NEVER use generic marketing templates like "This one [Topic] mistake...", "POV: you cracked...", "Stop scrolling...", "Nobody is talking about...", or "I tested 30 days of...". 
Every single hook must use a completely unique sentence structure, unexpected psychological trigger, or raw conversational hook that sounds nothing like a standard marketing ad.
Always output valid JSON matching the required schema.

CRITICAL ANTI-REPETITION RULES:
1. Never use generic AI clichés (e.g., "In today's fast-paced world", "Game-changer", "Unlock your potential", "Here's the secret nobody tells you").
2. The 3 hooks MUST use 3 completely different psychological triggers and sentence structures:
   - Hook 1: Curiosity gap or bold contrarian claim.
   - Hook 2: Direct callout, relatable pain point, or story-driven opener.
   - Hook 3: Actionable promise, shocking number, or pattern-interrupt question.
3. Make every script feel fresh, punchy, natural to speak out loud, and tailored specifically to the topic.

Respond ONLY with valid JSON matching this exact schema:
{
  "hooks": ["hook 1", "hook 2", "hook 3"],
  "script": [
    {"time": "0:00-0:03", "visual": "...", "audio": "..."},
    {"time": "0:03-0:15", "visual": "...", "audio": "..."},
    {"time": "0:15-0:25", "visual": "...", "audio": "..."}
  ],
  "cta": "...",
  "cta_why": "...",
  "caption": "...",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
}"""

def build_user_prompt(topic: str, tone: str) -> str:
    salt = f" [Ref#{random.randint(1000, 9999)}-{int(time.time())}]"
    unique_topic = topic + salt
    chosen_angle = random.choice(CREATIVE_ANGLES)
    return (
        f'Topic / niche: """{unique_topic}"""\n'
        f"Tone: {tone} - {TONE_GUIDES[tone]}\n"
        f"Creative Direction for this generation: {chosen_angle}\n"
        f"Generate a completely fresh, non-repetitive JSON response now."
    )

# --------------------------------------------------------------------------
# Lifespan & Clients Setup
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    app.state.openai = AsyncOpenAI(api_key=openai_key, timeout=30.0, max_retries=1) if openai_key else None

    # 2. Groq
    groq_key = os.getenv("GROQ_API_KEY")
    app.state.groq = AsyncOpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1", timeout=30.0) if groq_key else None

    # 3. Gemini
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and genai:
        genai.configure(api_key=gemini_key)
        app.state.gemini_available = True
    else:
        app.state.gemini_available = False

    # 4. Mistral (OpenAI-compatible)
    mistral_key = os.getenv("MISTRAL_API_KEY")
    app.state.mistral = AsyncOpenAI(api_key=mistral_key, base_url="https://api.mistral.ai/v1", timeout=30.0) if mistral_key else None

    # 5. Cohere key
    app.state.cohere_key = os.getenv("COHERE_API_KEY")

    yield
    
    for client in [app.state.openai, app.state.groq, app.state.mistral]:
        if client:
            await client.close()

app = FastAPI(title="HookMaster AI API", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Rate limiter
# --------------------------------------------------------------------------
_hits: Dict[str, Deque[float]] = defaultdict(deque)
free_attempts: Dict[str, int] = {}

def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MIN:
        retry_after = max(1, int(60 - (now - window[0])))
        raise HTTPException(status_code=429, detail="Too many requests.", headers={"Retry-After": str(retry_after)})
    window.append(now)

# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"service": "HookMaster AI API", "status": "ok", "models": ["openai", "groq", "gemini", "mistral", "cohere"]}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, request: Request):
    ip = client_ip(request)
    check_rate_limit(ip)

    if free_attempts.get(ip, 0) >= 3:
        raise HTTPException(status_code=403, detail="Free limit reached.")

    user_prompt = build_user_prompt(payload.topic, payload.tone)
    raw_content = None
    used_model_name = "unknown"

    # 1. OpenAI
    if request.app.state.openai and not raw_content:
        try:
            completion = await request.app.state.openai.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                temperature=0.95,
                max_tokens=1000,
            )
            raw_content = completion.choices[0].message.content
            used_model_name = f"OpenAI ({OPENAI_MODEL})"
        except Exception as e:
            logger.warning(f"OpenAI o'tkazib yuborildi: {e}")

    # 2. Groq
    if request.app.state.groq and not raw_content:
        try:
            completion = await request.app.state.groq.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                temperature=0.95,
                max_tokens=1000,
            )
            raw_content = completion.choices[0].message.content
            used_model_name = f"Groq ({GROQ_MODEL})"
        except Exception as e:
            logger.warning(f"Groq o'tkazib yuborildi: {e}")

    # 3. Gemini
    if request.app.state.gemini_available and not raw_content:
        try:
            g_model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                generation_config={"response_mime_type": "application/json"}
            )
            response = g_model.generate_content(user_prompt)
            raw_content = response.text
            used_model_name = f"Gemini ({GEMINI_MODEL})"
        except Exception as e:
            logger.warning(f"Gemini o'tkazib yuborildi: {e}")

    # 4. Mistral AI
    if request.app.state.mistral and not raw_content:
        try:
            completion = await request.app.state.mistral.chat.completions.create(
                model=MISTRAL_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                temperature=0.95,
                max_tokens=1000,
            )
            raw_content = completion.choices[0].message.content
            used_model_name = f"Mistral ({MISTRAL_MODEL})"
        except Exception as e:
            logger.warning(f"Mistral o'tkazib yuborildi: {e}")

    # 5. Cohere
    if request.app.state.cohere_key and not raw_content:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://api.cohere.com/v2/chat",
                    headers={"Authorization": f"Bearer {request.app.state.cohere_key}", "Content-Type": "application/json"},
                    json={
                        "model": COHERE_MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt}
                        ],
                        "response_format": {"type": "json_object"},
                    }
                )
                res.raise_for_status()
                data = res.json()
                raw_content = data["message"]["content"][0]["text"]
                used_model_name = f"Cohere ({COHERE_MODEL})"
        except Exception as e:
            logger.error(f"Cohere ham xatolik berdi: {e}")

    if not raw_content:
        raise HTTPException(status_code=500, detail="Barcha 5 ta AI serverida vaqtinchalik xatolik yuz berdi.")

    try:
        content = GeneratedContent.model_validate(json.loads(raw_content))
        free_attempts[ip] = free_attempts.get(ip, 0) + 1

        return GenerateResponse(
            **content.model_dump(),
            topic=payload.topic,
            tone=payload.tone,
            model=used_model_name,
        )
    except Exception as e:
        logger.exception("JSON validatsiya xatoligi")
        raise HTTPException(status_code=500, detail=f"AI javobini o'qishda xatolik: {str(e)}")

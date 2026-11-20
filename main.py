"""
HookMaster AI - FastAPI backend
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
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field, ValidationError, field_validator
import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hookmaster")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

WALLET_ADDRESS = "TNqhvttcMhmhqFnkmtZcaZRbYJyGqXFZBg"
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

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
# Prompt & Payment Verification Helper
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
    # DYNAMIC SALT: Keshni buzish va har safar yangi tahlil olish uchun tasodifiy qiymat qo'shamiz
    salt = f" [Ref#{random.randint(1000, 9999)}-{int(time.time())}]"
    unique_topic = topic + salt
    chosen_angle = random.choice(CREATIVE_ANGLES)
    return (
        f'Topic / niche: """{unique_topic}"""\n'
        f"Tone: {tone} - {TONE_GUIDES[tone]}\n"
        f"Creative Direction for this generation: {chosen_angle}\n"
        f"Generate a completely fresh, non-repetitive JSON response now."
    )

def check_latest_usdt_payment(expected_amount: float, used_txids: Optional[List[str]] = None):
    if used_txids is None:
        used_txids = []
    try:
        url = f"https://api.trongrid.io/v1/accounts/{WALLET_ADDRESS}/transactions/trc20"
        params = {"limit": 10, "contract_address": USDT_CONTRACT_ADDRESS}
        headers = {"accept": "application/json"}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        data = response.json()
        
        if "data" in data:
            for tx in data["data"]:
                if tx.get("to") == WALLET_ADDRESS:
                    tx_id = tx.get("transaction_id")
                    if tx_id in used_txids:
                        continue
                    amount = int(tx.get("value", 0)) / 1_000_000
                    if amount == float(expected_amount):
                        return {"status": True, "txid": tx_id, "amount": amount}
        return {"status": False}
    except Exception as e:
        logger.error(f"TronGrid API xatoligi: {e}")
        return {"status": False}

# --------------------------------------------------------------------------
# Rate limiter & App setup
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    api_key = os.getenv("OPENAI_API_KEY")
    app.state.openai = AsyncOpenAI(api_key=api_key, timeout=30.0, max_retries=2) if api_key else None
    yield
    if app.state.openai:
        await app.state.openai.close()

app = FastAPI(title="HookMaster AI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"service": "HookMaster AI API", "status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, request: Request):
    ip = client_ip(request)
    check_rate_limit(ip)

    if free_attempts.get(ip, 0) >= 1:
        raise HTTPException(status_code=403, detail="Free limit reached.")

    client: AsyncOpenAI | None = request.app.state.openai
    if client is None:
        raise HTTPException(status_code=500, detail="Server missing API key.")

    user_prompt = build_user_prompt(payload.topic, payload.tone)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        completion = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.98,
            frequency_penalty=0.8,
            presence_penalty=0.7,
            max_tokens=1000,
        )
        raw = completion.choices[0].message.content or "{}"
        content = GeneratedContent.model_validate(json.loads(raw))
        free_attempts[ip] = free_attempts.get(ip, 0) + 1

        return GenerateResponse(
            **content.model_dump(),
            topic=payload.topic,
            tone=payload.tone,
            model=OPENAI_MODEL,
        )
    except Exception as e:
        logger.exception("AI generation error")
        raise HTTPException(status_code=500, detail=str(e))

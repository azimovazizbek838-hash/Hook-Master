from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from openai import OpenAI

app = FastAPI(title="HookMaster AI")

# OpenAI klientini sozlash
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class HookRequest(BaseModel):
    topic: str
    tone: str = "viral"

@app.post("/api/generate")
def generate_hooks(data: HookRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional social media content creator and expert in viral TikTok & Reels hooks."},
                {"role": "user", "content": f"Generate 3 viral hooks and a 15-second script for the topic: {data.topic} with a {data.tone} tone."}
            ]
        ]
        result = response.choices[0].message.content
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Frontend index.html faylini to'g'ridan-to'g'ri brauzerga chiqarish
@app.get("/")
def serve_frontend():
    return FileResponse("index.html")


"""Gemini Flash vision integration for trading chart analysis."""

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types
from PIL import Image

MAX_HISTORY_EXCHANGES = 4

SYSTEM_PROMPT = (
    "You are a professional trading analyst specialising in technical analysis. "
    "Give direct, actionable signals based on what you see on the chart. "
    "Reasoning and exit conditions must always be written in Georgian (ქართული)."
)

ANALYSIS_PROMPT = """\
Analyze the trading chart in this screenshot and respond in EXACTLY this format — no extra text:

SIGNAL: [BUY or SELL or HOLD or UNCLEAR]
CONFIDENCE: [LOW or MEDIUM or HIGH]
REASONING: [1-2 წინადადება ქართულად: კონკრეტული ტექნიკური მიზეზი. არ აღწერო სანთლები — მიუთითე სიგნალი.]
EXIT: [1 წინადადება ქართულად: სად დახურო — კონკრეტული დონე, MA, RSI პირობა ან სტრუქტურული ნიშანი.]

Rules:
- UNCLEAR if the screenshot shows no chart
- HOLD if the setup is ambiguous or low-probability
- BUY/SELL only when there is a specific, high-probability technical reason
- EXIT must name a concrete price level or condition, not a vague phrase
"""


@dataclass
class AnalysisResult:
    signal: str         # BUY | SELL | HOLD | UNCLEAR | CHAT
    confidence: str     # LOW | MEDIUM | HIGH | —
    reasoning: str
    exit_condition: str
    timestamp: str
    raw_response: str
    is_chat: bool = False
    date: str = ""


def _resize(img: Image.Image, max_px: int = 1568) -> Image.Image:
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _strip_images(content: types.Content) -> types.Content:
    """Remove inline image parts from a Content object, keeping only text."""
    text_parts = [p for p in content.parts if p.text is not None]
    return types.Content(
        role=content.role,
        parts=text_parts or [types.Part(text="[screenshot]")],
    )


def _parse(text: str) -> dict[str, str]:
    patterns = {
        "signal":         r"SIGNAL:\s*(BUY|SELL|HOLD|UNCLEAR)",
        "confidence":     r"CONFIDENCE:\s*(LOW|MEDIUM|HIGH)",
        "reasoning":      r"REASONING:\s*(.+?)(?=\nEXIT:|$)",
        "exit_condition": r"EXIT:\s*(.+?)$",
    }
    out: dict[str, str] = {}
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
        out[key] = m.group(1).strip() if m else "UNKNOWN"
    return out


def analyze_screenshot(
    img: Image.Image,
    api_key: str,
    model_name: str = "gemini-1.5-flash",
    history: Optional[list] = None,
    user_prompt: Optional[str] = None,
    market_context: str = "",
) -> tuple["AnalysisResult", list]:
    if history is None:
        history = []

    client = genai.Client(api_key=api_key)
    is_chat = bool(user_prompt)

    if is_chat:
        prompt_text = user_prompt
    else:
        header = f"[{market_context}]\n\n" if market_context else ""
        prompt_text = header + ANALYSIS_PROMPT

    img = _resize(img)
    new_user = types.Content(
        role="user",
        parts=[
            types.Part(inline_data=types.Blob(data=_to_bytes(img), mime_type="image/png")),
            types.Part(text=prompt_text),
        ],
    )

    contents = history + [new_user]

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1024,
                system_instruction=SYSTEM_PROMPT,
            ),
        )
        raw = response.text or ""
    except Exception as exc:
        raw = f"Error: {exc}"

    # Build updated history: strip images from all previous user turns.
    new_history = [
        _strip_images(m) if m.role == "user" else m
        for m in history
    ]
    new_history.append(new_user)
    new_history.append(types.Content(
        role="model",
        parts=[types.Part(text=raw)],
    ))

    max_msgs = MAX_HISTORY_EXCHANGES * 2
    if len(new_history) > max_msgs:
        new_history = new_history[-max_msgs:]

    now = datetime.now()
    ts = now.strftime("%H:%M:%S")
    dt = now.strftime("%b %d, %Y")

    if is_chat:
        result = AnalysisResult(
            signal="CHAT", confidence="—",
            reasoning=raw, exit_condition="",
            timestamp=ts, raw_response=raw, is_chat=True, date=dt,
        )
    else:
        parsed = _parse(raw)
        result = AnalysisResult(
            signal=parsed.get("signal", "UNCLEAR"),
            confidence=parsed.get("confidence", "LOW"),
            reasoning=parsed.get("reasoning", ""),
            exit_condition=parsed.get("exit_condition", ""),
            timestamp=ts, raw_response=raw, is_chat=False, date=dt,
        )

    return result, new_history

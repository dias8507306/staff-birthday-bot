"""Тексты поздравлений от AI (Google Gemini). Ключ — GEMINI_API_KEY в .env (aistudio.google.com).

Модели пробуются по очереди (GEMINI_MODELS через запятую): у Gemini бывает «high demand» (503) — тогда берём следующую.
"""
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

DEFAULT_MODELS = ("gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-flash-latest,"
                  "gemini-3.5-flash-lite,gemini-flash-lite-latest")


def api_key() -> str:  # читаем при вызове: .env загружает bot.py уже после импорта этого модуля
    return os.getenv("GEMINI_API_KEY", "").strip()


def models() -> list:
    return [m.strip() for m in os.getenv("GEMINI_MODELS", DEFAULT_MODELS).split(",") if m.strip()]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SIGNATURE = "Ізгі тілекпен, EduApp командасы 💜"


def enabled() -> bool:
    return bool(api_key())


def build_prompt(person, examples) -> str:
    gender = {True: "әйел", False: "ер"}.get(person.get("female_known"))
    words = person["name"].split()
    given = words[1] if len(words) >= 2 else words[0]  # в таблице всегда «Фамилия Имя [Отчество]»
    lines = [
        "Сен EduApp білім беру компаниясының HR-ісің. Қызметкерге туған күнге арналған құттықтау мәтінін ҚАЗАҚ тілінде жаз.",
        "",
        f"Қызметкер: {person['name']}",
        f"Есімі (аты): {given} — құттықтауда тек осы есімді қолдан, тегін емес.",
        f"Лауазымы: {person['position']}" if person.get("position") else "",
        f"Жынысы: {gender}" if gender else "Жынысы белгісіз — есімі бойынша анықта.",
        "",
        "Ережелер:",
        "- Бірінші жол: «<Есімі>, туған күніңізбен! 🎉». Есімі — тегі емес, аты (әдетте екінші сөз: "
        "«Сапаров Ерлан» → «Ерлан»). Есімді қазақша дұрыс жаз.",
        "- Содан кейін бос жол және 2–3 сөйлем: оның жұмысына, лауазымына қатысты жылы, нақты тілек. "
        "Жалпы, жасанды сөздерсіз.",
        "- «Сіз» деп құрметпен жаз. Жасын атама.",
        f"- Соңғы жол: «{SIGNATURE}».",
        "- Барлығы 200–330 таңба.",
    ]
    if examples:
        lines += ["", "Біздің бұрынғы мәтіндердің үлгілері (стилі осындай болсын, бірақ сөзбе-сөз қайталама):", ""]
        lines += [f"---\n{e}" for e in examples]
    lines += ["", 'Жауапты тек JSON түрінде қайтар: {"gender": "ер" немесе "әйел", "text": "құттықтау мәтіні"}']
    return "\n".join(line for line in lines if line is not None)


def _call(model: str, prompt: str) -> dict:
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
    }).encode("utf-8")
    request = urllib.request.Request(URL.format(model=model), data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": api_key()})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse(data: dict):
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    raw = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    result = json.loads(raw)
    text = str(result.get("text", "")).strip()
    if len(text) < 40:
        raise ValueError("пустой текст")
    gender = str(result.get("gender", "")).lower()
    female = True if "әйел" in gender or "жен" in gender else False if gender.startswith(("ер", "муж", "male")) else None
    return text, female


def greeting(person, examples=()):
    """Текст поздравления для сотрудника: (текст, female | None). Бросает RuntimeError, если ни одна модель не ответила."""
    if not api_key():
        raise RuntimeError("нет GEMINI_API_KEY в .env")
    examples = random.sample(list(examples), min(3, len(examples)))
    prompt = build_prompt(person, examples)
    errors = []
    for model in models():
        for attempt in range(1):
            try:
                return _parse(_call(model, prompt))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:200]
                errors.append(f"{model}: {error.code} {detail}")
                if error.code not in (429, 500, 503):
                    break  # модель недоступна этому ключу — сразу следующая
            except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as error:
                errors.append(f"{model}: {error}")
            time.sleep(1)
    raise RuntimeError("; ".join(errors[-3:]))

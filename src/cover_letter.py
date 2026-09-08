from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from .profile import profile_as_prompt_block


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def generate_cover_letter_template(
    profile: dict[str, Any],
    *,
    company: str,
    role: str,
    location: str = "",
    job_description: str = "",
) -> str:
    name = profile.get("full_name", "") or "[Your name]"
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    edu = profile.get("education", {})
    skills = ", ".join(profile.get("skills", [])[:8])
    highlights = profile.get("experience_highlights", [])[:3]
    highlights_txt = "\n".join(f"- {h}" for h in highlights)

    loc_bit = f" in {location}" if location else ""
    jd_bit = ""
    if job_description:
        snippet = " ".join(job_description.split())[:280]
        jd_bit = f"\n\nI was particularly drawn to this opportunity because it aligns with: {snippet}"

    contact = " | ".join(value for value in (email, phone) if value)
    contact_block = f"\n{contact}" if contact else ""
    degree = str(edu.get("degree", "")).strip()
    school = str(edu.get("school", "")).strip()
    classification = str(edu.get("classification", "")).strip()
    end = str(edu.get("end", "")).strip()
    education_bits = " ".join(
        bit for bit in (
            f"a {degree}" if degree else "a student or graduate",
            f"at {school}" if school else "",
            f"({classification})" if classification else "",
            f"and graduating {end}" if end else "",
        ) if bit
    )
    summary = str(profile.get("summary", "")).strip()
    summary_sentence = f" {summary}" if summary else ""

    body = f"""{name}{contact_block}

{date.today().strftime("%d %B %Y")}

Dear Hiring Manager,

I am writing to apply for the {role} position at {company}{loc_bit}. I am {education_bits}.{summary_sentence}

My experience combines legal training with analytical and technical project work:
{highlights_txt}

Key strengths I would bring to {company} include {skills}. I am motivated, collaborative, and comfortable working with both legal material and data-driven tools.
{jd_bit}

I would welcome the opportunity to contribute to your team and discuss how my background can support {company}'s work. Thank you for considering my application.

Yours sincerely,
{name}
"""
    return _clean(body)


def generate_cover_letter(
    profile: dict[str, Any],
    *,
    company: str,
    role: str,
    location: str = "",
    job_description: str = "",
    use_ai: bool = True,
) -> str:
    if use_ai and os.getenv("OPENAI_API_KEY"):
        try:
            return _generate_with_openai(
                profile,
                company=company,
                role=role,
                location=location,
                job_description=job_description,
            )
        except Exception:
            pass
    return generate_cover_letter_template(
        profile,
        company=company,
        role=role,
        location=location,
        job_description=job_description,
    )


def _generate_with_openai(
    profile: dict[str, Any],
    *,
    company: str,
    role: str,
    location: str,
    job_description: str,
) -> str:
    from openai import OpenAI

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = f"""Write a professional UK-style cover letter (250-350 words).
Tone: confident, concise, specific, no fluff or clichés.
Do not invent experience. Use only the candidate profile.
Formatting: use plain text with no bold body text, salutation, complimentary close,
or applicant name. Use bold only if an employer-supplied template explicitly requires it.

Role: {role}
Company: {company}
Location: {location or 'not specified'}

Job description:
{job_description[:4000] or 'Not provided'}

Candidate profile:
{profile_as_prompt_block(profile)}
"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You write tailored graduate/internship cover letters for law and tech-law roles.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    return _clean(resp.choices[0].message.content or "")


def save_cover_letter(text: str, output_dir: str | Path, filename: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    path.write_text(text, encoding="utf-8")
    return path

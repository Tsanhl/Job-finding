"""Conservative advert excerpts and expiry; never invent missing vacancy facts."""

import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup


def requirement_sections(value):
    if not isinstance(value, str):
        value = str(value or "")
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    # Keep inline emphasis inside its sentence; only block boundaries split text.
    for node in soup.find_all(["p", "li", "div", "h2", "h3", "h4", "br"]):
        node.append("\n")
    text = soup.get_text(" ", strip=False)
    chunks = [
        re.sub(r"\s+", " ", x).strip(" •-")
        for x in re.split(r"\n|(?<=[.!?;])\s+", text)
    ]
    buckets = {"academic": [], "skills": [], "other": []}
    for chunk in chunks:
        if len(chunk) < 10:
            continue
        if re.fullmatch(
            r"(requirements|qualifications|skills|experience|about you|what you need)\s*:?\s*",
            chunk,
            re.I,
        ):
            continue
        # Training and programme descriptions are benefits, not entry criteria.
        if re.search(
            r"\b(training|rotations? are|we (?:work|offer|provide|support)|you will (?:learn|develop|gain)|skills through|develops? a broad range|skills and knowledge to thrive)\b",
            chunk,
            re.I,
        ) and not re.search(
            r"\b(required|essential|must|need to|applicants|candidates must)\b",
            chunk,
            re.I,
        ):
            continue
        if re.search(
            r"\b(degree|bachelor|master|phd|doctorate|undergraduate|postgraduate|university|2:1|2:2|qualification|graduation|graduat(?:ed|ing)|A.level)\b",
            chunk,
            re.I,
        ):
            key = "academic"
        elif re.search(
            r"\b(skill|proficien|experience|knowledge|ability|able to|programming|python|excel|analytical|communication|teamwork|SQL|coding)",
            chunk,
            re.I,
        ):
            if not re.search(
                r"\b(required|essential|preferred|desirable|must|need|you have|you bring|your skills|applicants|candidates|strong|excellent|good|demonstrat\w*|proficien\w*|experience (?:in|with|of)|knowledge (?:in|of)|ability to|able to)\b",
                chunk,
                re.I,
            ) and not re.match(
                r"^(?:Python|SQL|Java|Excel|Programming|Coding|Analytical|Communication|Teamwork)\b",
                chunk,
                re.I,
            ):
                continue
            key = "skills"
        elif re.search(
            r"\b(require|must|eligible|right to work|sponsor|licen[cs]e|travel|available|residen)",
            chunk,
            re.I,
        ):
            key = "other"
        else:
            continue
        if chunk not in buckets[key]:
            buckets[key].append(chunk)
    # Excerpts stay verbatim, with explicit truncation instead of paraphrased claims.
    return {
        k: [x if len(x) <= 220 else x[:217].rstrip() + "…" for x in v[:3]]
        for k, v in buckets.items()
    }


def expired(job, now=None):
    now = now or datetime.now().astimezone()
    if str(job.get("status", "")).casefold() in {"closed", "expired"}:
        return True
    raw = str(
        job.get("closing_date") or job.get("closes_at") or job.get("deadline") or ""
    ).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw).date() < now.date()
        closing = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # An unzoned time or relative deadline is not enough evidence for removal.
        return closing.tzinfo is not None and closing < now.astimezone(timezone.utc)
    except ValueError:
        return False

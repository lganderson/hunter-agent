"""Local provider settings stored outside committed frontend source."""

import base64
import html
import json
import os
import re
import shutil
import string
import sys
import zlib
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from . import paths


MAX_RESUME_BYTES = 8 * 1024 * 1024
MAX_RESUME_TEXT_CHARS = 120_000
RESUME_CONTEXT_CHARS = 20_000
FIT_PROFILE_CONTEXT_CHARS = 3_000
RESUME_PREVIEW_CHARS = 800
RESUME_DIR_NAME = "resume"
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
SEARCH_GOALS_CONTEXT_CHARS = 20_000
FIT_SIGNAL_TEXT_CHARS = 20_000
DEFAULT_SEARCH_GOALS = """Main thesis:
I'm targeting senior product/program roles where creative, technical, or customer-facing teams need tools, workflows, platforms, and launch execution to ship better experiences.

Primary:
Player, creator, developer, media, and interactive customer experiences.

Secondary:
Builder productivity, developer platforms, technical workflow systems, product-led SaaS, commerce/platform workflows, and customer-facing service operations.

Exploration:
Robotics, simulation, connectivity, satellite/space service delivery, and complex technical operations platforms.

Backup/downrank:
General PMO, non-technical program management, pure infrastructure operations, compliance-heavy programs, internal-only process roles, and roles without clear product/platform/customer impact."""
DEFAULT_FIT_SIGNALS = {
    "role_terms": """technical program manager | 42
technical product manager | 42
technical project manager | 34
program manager | 34
product manager | 34
product management | 34
program management | 34
product operations manager | 30
program operations manager | 30
game producer | 28
producer | 24
product owner | 22
technical program | 26
technical product | 26
program lead | 20
product lead | 20
release manager | 18
project manager | 18
tpm | 28""",
    "domain_terms": """ai | 16
machine learning | 16
ml | 10
data science | 14
data | 9
platform | 14
developer platform | 18
developer tools | 18
api | 10
infrastructure | 10
web | 8
mobile | 8
commerce | 8
saas | 10
creator | 14
player experience | 14
game development | 14
media | 12
interactive | 10
customer experience | 10
service operations | 10
robotics | 12
simulation | 12
connectivity | 10
satellite | 8
online service | 8
live service | 8
release | 8
operations | 7""",
    "seniority_terms": """senior | 8
lead | 8
principal | 7
staff | 7
iii | 6""",
    "search_terms": """technical program manager
technical program manager iii
technical product manager
product manager
project manager
program manager
producer
product operations manager
release manager""",
    "low_match_terms": """account executive
sales
recruiter
intern
undergraduate
warehouse
retail associate""",
    "exclusion_terms": """account executive
accounts receivable
finance operations
general pmo
non-technical program management
pure infrastructure operations
compliance-heavy program
internal-only process
legal program manager
program manager intern
recruiter
retail associate
sales
security program manager
warehouse""",
    "strength_terms": """roadmap
product strategy
program management
launch
cross-functional
stakeholder
dependency
release readiness
execution
delivery
workflow
platform
customer experience
service delivery
requirements""",
}


def load_settings():
    if not paths.SETTINGS_FILE.exists():
        return {}
    with paths.SETTINGS_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def settings_status():
    settings = load_settings()
    return {
        "provider": settings.get("provider", ""),
        "model": settings.get("model", ""),
        "api_base": settings.get("api_base", ""),
        "search_goals": read_search_goals(settings),
        "fit_signals": read_fit_signals(settings),
        "token_configured": bool(settings.get("api_token")),
        "resume": resume_status(settings),
    }


def save_settings(provider, model, api_base, token, search_goals=None, fit_signals=None):
    paths.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    if provider is not None:
        settings["provider"] = provider
    if model is not None:
        settings["model"] = model
    if api_base is not None:
        settings["api_base"] = api_base
    if search_goals is not None:
        settings["search_goals"] = clean_settings_text(search_goals, SEARCH_GOALS_CONTEXT_CHARS)
    if fit_signals is not None:
        settings["fit_signals"] = normalize_fit_signals(fit_signals)
    if token:
        settings["api_token"] = token
    write_settings(settings)
    return settings_status()


def resume_status(settings=None):
    settings = settings or load_settings()
    resume = settings.get("resume") or {}
    text = read_resume_text()
    preview = text[:RESUME_PREVIEW_CHARS]
    return {
        "filename": resume.get("filename", ""),
        "uploaded_at": resume.get("uploaded_at", ""),
        "text_char_count": len(text),
        "extraction_status": resume.get("extraction_status", ""),
        "preview": preview,
        "preview_char_count": len(preview),
        "preview_truncated": len(text) > len(preview),
        "configured": bool(text),
    }


def save_resume_upload(filename, content_base64):
    if not filename:
        raise ValueError("Resume filename is required.")
    try:
        content = base64.b64decode(content_base64 or "", validate=True)
    except Exception as exc:  # noqa: BLE001 - surface invalid local upload payloads.
        raise ValueError(f"Resume upload is not valid base64: {exc}") from exc
    if not content:
        raise ValueError("Resume file is empty.")
    if len(content) > MAX_RESUME_BYTES:
        raise ValueError("Resume file is too large. Use a file smaller than 8 MB.")

    safe_name = safe_resume_filename(filename)
    extension = Path(safe_name).suffix.lower()
    text, extraction_status = extract_resume_text(safe_name, content)
    if not text:
        extraction_status = extraction_status or "No text could be extracted."

    resume_dir = resume_dir_path()
    resume_dir.mkdir(parents=True, exist_ok=True)
    for previous in resume_dir.glob("current*"):
        if previous.is_file():
            previous.unlink()
    original_path = resume_dir / f"current{extension or '.txt'}"
    text_path = resume_dir / "current.txt"
    original_path.write_bytes(content)
    text_path.write_text(text[:MAX_RESUME_TEXT_CHARS], encoding="utf-8")

    settings = load_settings()
    settings["resume"] = {
        "filename": safe_name,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "stored_file": original_path.name,
        "text_file": text_path.name,
        "extraction_status": extraction_status,
    }
    write_settings(settings)
    return settings_status()


def delete_resume():
    settings = load_settings()
    settings.pop("resume", None)
    if resume_dir_path().exists():
        shutil.rmtree(resume_dir_path())
    write_settings(settings)
    return settings_status()


def resume_context(max_chars=RESUME_CONTEXT_CHARS):
    settings = load_settings()
    resume = settings.get("resume") or {}
    text = read_resume_text()
    if not text:
        return ""
    filename = resume.get("filename", "Uploaded resume")
    return f"Uploaded resume context from {filename}:\n{text[:max_chars]}"


def fit_profile_context(max_chars=FIT_PROFILE_CONTEXT_CHARS):
    settings = load_settings()
    resume = settings.get("resume") or {}
    resume_text = read_resume_text()
    goals = read_search_goals(settings)
    signals = read_fit_signals(settings)
    parts = []
    if resume_text:
        combined = f"{resume_text}\n\n{goals}"
        filename = resume.get("filename", "Uploaded resume")
        parts.extend(
            [
                f"Uploaded resume: {filename} ({len(resume_text)} extracted characters).",
                list_line("Likely roles", matched_terms(combined, term_names(weighted_fit_terms("role_terms", signals)))),
                list_line("Domains and systems", matched_terms(combined, term_names(weighted_fit_terms("domain_terms", signals)))),
                list_line("Execution strengths", matched_terms(combined, plain_fit_terms("strength_terms", signals))),
            ]
        )
    if goals:
        parts.append(search_goals_context(max_chars=900))
    if not parts:
        return ""
    parts.append("Full resume text is available through hunter_get_resume_text when exact resume detail is needed.")
    return clean_settings_text("\n".join(part for part in parts if part), max_chars)


def read_search_goals(settings=None):
    settings = settings or load_settings()
    return clean_settings_text(settings.get("search_goals", DEFAULT_SEARCH_GOALS), SEARCH_GOALS_CONTEXT_CHARS)


def normalize_fit_signals(value):
    incoming = value if isinstance(value, dict) else {}
    normalized = {}
    for key, default_value in DEFAULT_FIT_SIGNALS.items():
        raw_value = incoming.get(key, default_value)
        normalized[key] = clean_settings_text(str(raw_value or ""), FIT_SIGNAL_TEXT_CHARS)
    return normalized


def read_fit_signals(settings=None):
    settings = settings or load_settings()
    return normalize_fit_signals(settings.get("fit_signals") or {})


def parse_fit_signal_lines(value):
    rows = []
    for raw_line in re.split(r"[\n,]", value or ""):
        line = clean_settings_text(raw_line, 1_000)
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    return rows


def weighted_fit_terms(key, signals=None):
    signals = signals or read_fit_signals()
    terms = []
    for row in parse_fit_signal_lines(signals.get(key, "")):
        parts = [part.strip() for part in row.split("|")]
        phrase = clean_settings_text(parts[0], 200).lower()
        if not phrase:
            continue
        try:
            weight = int(parts[1]) if len(parts) > 1 and parts[1] else 10
        except ValueError:
            weight = 10
        terms.append((phrase, max(0, min(100, weight))))
    return list(dict.fromkeys(terms))


def plain_fit_terms(key, signals=None):
    signals = signals or read_fit_signals()
    return list(dict.fromkeys(row.lower() for row in parse_fit_signal_lines(signals.get(key, "")) if row))


def role_fit_terms():
    return weighted_fit_terms("role_terms")


def domain_fit_terms():
    return weighted_fit_terms("domain_terms")


def seniority_fit_terms():
    return weighted_fit_terms("seniority_terms")


def low_match_terms():
    return plain_fit_terms("low_match_terms")


def role_exclusion_terms():
    return plain_fit_terms("exclusion_terms")


def search_terms():
    return plain_fit_terms("search_terms")


def term_names(weighted_terms):
    return [term for term, _weight in weighted_terms]


def search_goals_context(max_chars=SEARCH_GOALS_CONTEXT_CHARS):
    text = read_search_goals()
    signals = read_fit_signals()
    role_focus = ", ".join(term_names(weighted_fit_terms("role_terms", signals))[:12])
    search_focus = ", ".join(plain_fit_terms("search_terms", signals)[:8])
    signal_parts = []
    if role_focus:
        signal_parts.append(f"Target role signals: {role_focus}.")
    if search_focus:
        signal_parts.append(f"Career search terms: {search_focus}.")
    signal_text = "\n".join(signal_parts)
    if not text and not signal_text:
        return ""
    return f"Search Goals:\n{signal_text}\n\n{text[:max_chars]}"


def fit_context():
    parts = [read_resume_text(), search_goals_context()]
    return "\n\n".join(part for part in parts if part)


def matched_terms(text, terms):
    normalized = text.lower()
    return [term for term in terms if text_supports_term(normalized, term)]


def text_supports_term(text, term):
    normalized = term.lower()
    if re.search(rf"\b{re.escape(normalized)}\b", text):
        return True
    tokens = [token for token in re.split(r"[^a-z0-9+]+", normalized) if token]
    return len(tokens) > 1 and all(re.search(rf"\b{re.escape(token)}\b", text) for token in tokens)


def list_line(label, values):
    if not values:
        return f"{label}: not detected in compact profile."
    return f"{label}: {', '.join(dict.fromkeys(values))}."


def resume_text_payload():
    settings = load_settings()
    resume = settings.get("resume") or {}
    text = read_resume_text()
    return {
        "filename": resume.get("filename", ""),
        "text": text,
        "text_char_count": len(text),
        "configured": bool(text),
    }


def read_resume_text():
    text_path = resume_dir_path() / "current.txt"
    if not text_path.exists():
        return ""
    return text_path.read_text(encoding="utf-8", errors="replace").strip()


def clean_settings_text(value, max_chars):
    text = storage_clean(str(value or ""))
    return text[:max_chars]


def storage_clean(value):
    return re.sub(r"\r\n?", "\n", value).strip()


def resume_dir_path():
    return paths.DATA_DIR / RESUME_DIR_NAME


def safe_resume_filename(filename):
    name = Path(filename).name.strip()
    safe_chars = f"-_.() {string.ascii_letters}{string.digits}"
    cleaned = "".join(ch for ch in name if ch in safe_chars).strip()
    return cleaned or "resume.txt"


def extract_resume_text(filename, content):
    extension = Path(filename).suffix.lower()
    if extension in TEXT_EXTENSIONS:
        return clean_resume_text(content.decode("utf-8", errors="replace")), "ok"
    if extension == ".docx":
        return extract_docx_text(content)
    if extension == ".pdf":
        return extract_pdf_text(content)
    return "", "Unsupported resume file type. Upload .pdf, .docx, .txt, or .md."


def extract_docx_text(content):
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            document = archive.read("word/document.xml")
    except Exception as exc:  # noqa: BLE001 - malformed local files should become status text.
        return "", f"Could not read DOCX text: {exc}"
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        return "", f"Could not parse DOCX text: {exc}"
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return clean_resume_text("\n".join(paragraphs)), "ok"


def extract_pdf_text(content):
    text = extract_pdf_text_with_optional_library(content)
    if text:
        return clean_resume_text(text), "ok"
    text = extract_pdf_text_from_streams(content)
    if text:
        return clean_resume_text(text), "ok: best-effort PDF extraction"
    return "", "Could not extract PDF text. Try uploading a DOCX or TXT resume."


def extract_pdf_text_with_optional_library(content):
    ensure_optional_python_package_paths()
    text = extract_pdf_text_with_pdfplumber(content)
    if text:
        return text
    return extract_pdf_text_with_pypdf(content)


def extract_pdf_text_with_pdfplumber(content):
    try:
        import pdfplumber  # type: ignore
    except Exception:  # noqa: BLE001 - pdfplumber is optional.
        return ""
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:  # noqa: BLE001 - fall through to pypdf/lightweight parser.
        return ""


def extract_pdf_text_with_pypdf(content):
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:  # noqa: BLE001 - pypdf is optional.
        return ""
    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001 - fall through to lightweight parser.
        return ""


def ensure_optional_python_package_paths():
    for path in optional_python_package_paths():
        text_path = str(path)
        if text_path not in sys.path and path.exists():
            sys.path.append(text_path)


def optional_python_package_paths():
    paths_from_env = [
        Path(item)
        for item in os.environ.get("HUNTER_PYTHON_PACKAGE_PATHS", "").split(os.pathsep)
        if item
    ]
    local_envs = [
        paths.ROOT / ".venv",
        paths.ROOT / "venv",
    ]
    for env_path in local_envs:
        paths_from_env.extend((env_path / "lib").glob("python*/site-packages"))
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "lib"
    paths_from_env.extend(bundled.glob("python*/site-packages"))
    return paths_from_env


def extract_pdf_text_from_streams(content):
    raw = content.decode("latin-1", errors="ignore")
    chunks = []
    for stream in re.findall(r"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.S):
        data = stream.encode("latin-1", errors="ignore")
        for candidate in inflate_candidates(data):
            chunks.extend(pdf_text_literals(candidate))
    return "\n".join(chunks)


def inflate_candidates(data):
    yield data.decode("latin-1", errors="ignore")
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            yield zlib.decompress(data.strip(), wbits).decode("latin-1", errors="ignore")
        except zlib.error:
            continue


def pdf_text_literals(value):
    pieces = []
    for literal in re.findall(r"\(((?:\\.|[^\\()])*)\)\s*Tj", value):
        pieces.append(unescape_pdf_literal(literal))
    for array in re.findall(r"\[(.*?)\]\s*TJ", value, flags=re.S):
        for literal in re.findall(r"\((?:\\.|[^\\()])*\)", array):
            pieces.append(unescape_pdf_literal(literal[1:-1]))
    return [piece for piece in pieces if piece.strip()]


def unescape_pdf_literal(value):
    value = re.sub(r"\\([nrtbf()\\])", lambda match: {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "",
        "f": "",
        "(": "(",
        ")": ")",
        "\\": "\\",
    }[match.group(1)], value)
    value = re.sub(r"\\([0-7]{1,3})", lambda match: chr(int(match.group(1), 8)), value)
    return html.unescape(value)


def clean_resume_text(value):
    value = re.sub(r"\r\n?", "\n", value or "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def write_settings(settings):
    paths.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with paths.SETTINGS_FILE.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    try:
        os.chmod(paths.SETTINGS_FILE, 0o600)
    except OSError:
        pass

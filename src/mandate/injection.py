import re
import unicodedata
from typing import Literal, NamedTuple

Severity = Literal["strong", "medium"]

_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad]")
_INVISIBLE_SIGNAL = re.compile("[\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_TAGS = re.compile("[\U000e0000-\U000e007f]")


class Signal(NamedTuple):
    field: str
    name: str
    severity: Severity
    snippet: str


class Pattern(NamedTuple):
    name: str
    severity: Severity
    regex: re.Pattern[str]


def _p(name: str, severity: Severity, pattern: str) -> Pattern:
    return Pattern(name, severity, re.compile(pattern, re.IGNORECASE))


_SUBJECT = r"(?:ai|assistants?|agents?|models?|llms?|bots?)"
_RECIPIENT = (
    r"(?:the\s+)?(?:payment\s+|safety\s+)?"
    r"(?:reviewers?|assistants?|ai agents?|ai|agents?|models?|llms?|bots?"
    r"|automated (?:buyers?|agents?))"
)

PATTERNS = [
    _p(
        "override_instructions",
        "strong",
        r"\b(?:ignore|ignora|ignorez|ignoriere|disregard|forget|override|bypass|neglect)\b"
        r"[^.\n]{0,50}?\b(?:instructions?|instrucciones|anweisungen|rules?|reglas|guidelines?"
        r"|policies|constraints|limits?|prompts?)\b",
    ),
    _p(
        "role_hijack",
        "strong",
        r"\byou are now\b|\bnew instructions?\s*:|\bsystem prompt\b|\bdeveloper mode\b"
        r"|\bjailbreak\b|(?:^|\n)\s*(?:system|assistant)\s*:"
        r"|\"role\"\s*:\s*\"(?:system|assistant)\""
        r"|<\|(?:im_start|im_end|system|assistant|endoftext)\|>|\[/?inst\]|<<sys>>",
    ),
    _p(
        "approval_bypass",
        "strong",
        r"\b(?:approve|authori[sz]e|pay|complete|process|confirm)\b[^.\n]{0,60}?"
        r"\b(?:without|no need for|skipping|skip)\b[^.\n]{0,30}?"
        r"\b(?:review|approval|confirmation|checks?|verification|the user|human|asking)\b"
        r"|\b(?:skip|bypass|disable|turn off|circumvent)\b[^.\n]{0,25}?"
        r"\b(?:approval|review|verification|checks?|safety|safeguards?|limits?|thresholds?)\b",
    ),
    _p(
        "claimed_preapproval",
        "strong",
        r"\b(?:pre-?approved|already (?:been )?(?:approved|authori[sz]ed)"
        r"|(?:user|owner|account holder|admin(?:istrator)?)\s+(?:has\s+|have\s+)?(?:already\s+)?"
        r"(?:approved|authori[sz]ed)|no further (?:checks?|approval|review|verification))\b",
    ),
    _p(
        "hide_from_user",
        "strong",
        r"\b(?:do not|don'?t|never|without)\b[^.\n]{0,30}?"
        r"\b(?:tell|inform|notify|alert|mention|show|let)\b[^.\n]{0,25}?"
        r"\b(?:the )?(?:user|owner|human|customer|admin)\b",
    ),
    _p(
        "reviewer_reply",
        "strong",
        r"\b(?:reply|respond|answer|output)\b[^.\n]{0,25}?\b(?:verdict|match|approve|approved)\b",
    ),
    _p(
        "agent_directive",
        "strong",
        rf"\b{_SUBJECT}\b[^.\n]{{0,25}}?\b(?:must|should|shall|needs? to|has to|have to"
        r"|is required to|are required to)\b[^.\n]{0,40}?"
        r"\b(?:pay|approve|purchase|buy|transfer|send|wire|authori[sz]e|ignore)\b",
    ),
    _p(
        "limit_evasion",
        "strong",
        r"\b(?:stay|keep|remain)\b[^.\n]{0,20}?\b(?:under|below|within)\b[^.\n]{0,20}?"
        r"\b(?:limits?|thresholds?|caps?|budget)\b"
        r"|\bsplit\b[^.\n]{0,40}?\b(?:payments?|orders?|purchases?|transactions?)\b[^.\n]{0,50}?"
        r"\b(?:limits?|thresholds?|caps?|review|approval|detection)\b"
        r"|\b(?:mandate|(?:spending|payment|budget|approval) limits?|safeguards?)\b[^.\n]{0,30}?"
        r"\b(?:do(?:es)? not|don'?t|no longer)\s+apply\b"
        r"|\b(?:spending|payment|approval) limits?\b[^.\n]{0,20}?\b(?:waived|lifted|suspended)\b",
    ),
    _p(
        "decode_and_execute",
        "strong",
        r"\b(?:decode|decrypt|base64)\b[^.\n]{0,40}?\b(?:and|then)\s+(?:follow|execute|run|obey|apply)\b",
    ),
    _p(
        "addressed_to_ai",
        "medium",
        r"\b(?:note|message|notice|attention|tip|hey|hello|dear|instructions?)\b[^.\n]{0,15}?"
        rf"\b(?:to|for)\s+{_RECIPIENT}\b\s*[:,\-\u2013\u2014]"
        rf"|(?:^|\n)\s*(?:to|dear|attention)\s+{_RECIPIENT}\b\s*[:,\-\u2013\u2014]",
    ),
    _p(
        "hidden_content",
        "medium",
        r"<!--|display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0\b|opacity\s*:\s*0\b"
        r"|aria-hidden",
    ),
    _p("base64_blob", "medium", r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{60,}={0,2}(?![A-Za-z0-9+/=])"),
    _p("exfil_link", "medium", r"!\[[^\]]*\]\(https?://[^)\s]*[?&][^)\s]*\)"),
    _p("wallet_address", "medium", r"\b0x[0-9a-fA-F]{40}\b|\bpaypal\.me/"),
    _p(
        "payment_redirect",
        "medium",
        r"\b(?:payments?|funds|money|amount|balance)\b[^.\n]{0,40}?"
        r"\b(?:must|should|needs? to|has to|have to)\b[^.\n]{0,20}?"
        r"\b(?:be\s+)?(?:sent|paid|transferred|wired|redirected)\b[^.\n]{0,20}?\b(?:to|into)\b",
    ),
    _p(
        "urgency",
        "medium",
        r"\b(?:urgent(?:ly)?|immediately|right now|act now|asap"
        r"|within \d+ (?:minutes?|seconds?|hours?))\b",
    ),
]


def normalize(text: str) -> tuple[str, list[Signal]]:
    extras: list[Signal] = []
    decoded = "".join(chr(ord(c) - 0xE0000) for c in text if 0xE0000 <= ord(c) <= 0xE007F)
    if decoded:
        extras.append(Signal("", "unicode_tag_smuggling", "strong", decoded[:80]))
    if _INVISIBLE_SIGNAL.search(text):
        extras.append(Signal("", "invisible_characters", "medium", ""))
    cleaned = _TAGS.sub("", text)
    cleaned = _INVISIBLE.sub("", cleaned)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    if decoded:
        cleaned = f"{cleaned} {decoded}"
    return cleaned, extras


def scan(field: str, text: str) -> list[Signal]:
    if not text:
        return []
    cleaned, signals = normalize(text)
    found = [signal._replace(field=field) for signal in signals]
    for pattern in PATTERNS:
        match = pattern.regex.search(cleaned)
        if match:
            found.append(Signal(field, pattern.name, pattern.severity, match.group(0)[:80]))
    return found


def scan_fields(fields: dict[str, str]) -> list[Signal]:
    signals: list[Signal] = []
    for field, text in fields.items():
        signals.extend(scan(field, text))
    return signals

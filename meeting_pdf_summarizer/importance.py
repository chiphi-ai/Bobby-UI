"""Importance scoring and filtering for PDF content."""
import re
from typing import List, Tuple, Dict


# Keywords that indicate important content
ACTION_KEYWORDS = [
    'action', 'task', 'todo', 'assign', 'owner', 'due', 'deadline', 'deliverable',
    'next step', 'follow up', 'complete', 'finish', 'implement'
]

DECISION_KEYWORDS = [
    'decide', 'decision', 'approve', 'approval', 'agree', 'agreement', 'consensus',
    'vote', 'chosen', 'selected', 'final', 'approved'
]

OUTCOME_KEYWORDS = [
    'outcome', 'result', 'conclusion', 'summary', 'key finding', 'takeaway',
    'achievement', 'milestone', 'delivered', 'completed'
]

RISK_KEYWORDS = [
    'risk', 'blocker', 'issue', 'problem', 'concern', 'challenge', 'obstacle',
    'dependency', 'constraint', 'limitation', 'warning'
]

METRIC_KEYWORDS = [
    'metric', 'kpi', 'number', 'percent', '%', 'increase', 'decrease', 'growth',
    'target', 'goal', 'budget', 'cost', 'revenue', 'users', 'customers'
]

DATE_PATTERNS = [
    r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',  # MM/DD/YYYY
    r'\d{4}[/-]\d{1,2}[/-]\d{1,2}',  # YYYY/MM/DD
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}',  # Month DD, YYYY
    r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}',  # DD Month YYYY
]

EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
PHONE_PATTERN = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b|\b\(\d{3}\)\s?\d{3}[-.]?\d{4}\b'


def score_sentence_importance(sentence: str) -> float:
    """
    Score a sentence's importance (0.0 to 1.0).
    Higher scores indicate more important content.
    """
    score = 0.0
    sentence_lower = sentence.lower()
    
    # Check for action items
    for keyword in ACTION_KEYWORDS:
        if keyword in sentence_lower:
            score += 0.15
            break
    
    # Check for decisions
    for keyword in DECISION_KEYWORDS:
        if keyword in sentence_lower:
            score += 0.15
            break
    
    # Check for outcomes
    for keyword in OUTCOME_KEYWORDS:
        if keyword in sentence_lower:
            score += 0.12
            break
    
    # Check for risks
    for keyword in RISK_KEYWORDS:
        if keyword in sentence_lower:
            score += 0.12
            break
    
    # Check for metrics/numbers
    has_number = bool(re.search(r'\d+', sentence))
    for keyword in METRIC_KEYWORDS:
        if keyword in sentence_lower:
            score += 0.1
            break
    if has_number and any(kw in sentence_lower for kw in ['%', 'percent', 'increase', 'decrease']):
        score += 0.08
    
    # Check for dates
    for pattern in DATE_PATTERNS:
        if re.search(pattern, sentence, re.IGNORECASE):
            score += 0.1
            break
    
    # Penalize very short or very long sentences
    word_count = len(sentence.split())
    if word_count < 3:
        score *= 0.5
    elif word_count > 50:
        score *= 0.8
    
    # Penalize boilerplate
    boilerplate_phrases = [
        'thank you', 'please find', 'see attached', 'best regards', 'sincerely',
        'page', 'table of contents', 'confidential', 'proprietary'
    ]
    for phrase in boilerplate_phrases:
        if phrase in sentence_lower:
            score *= 0.3
            break
    
    return min(score, 1.0)


def identify_important_sections(text: str, top_k: int = 50) -> List[Tuple[str, float]]:
    """
    Identify the most important sentences from text.
    
    Returns:
        List of (sentence, score) tuples, sorted by score descending
    """
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Score each sentence
    scored = [(s.strip(), score_sentence_importance(s.strip())) for s in sentences if s.strip()]
    
    # Sort by score and return top K
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def extract_action_items(text: str) -> List[Dict]:
    """Extract action items from text (best-effort)."""
    action_items = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(kw in sentence_lower for kw in ACTION_KEYWORDS):
            # Try to extract owner and due date
            owner = "Unassigned"
            due = "Not specified"
            
            # Look for "assigned to", "owner:", etc.
            owner_match = re.search(r'(?:assigned to|owner|responsible|by)\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', sentence, re.IGNORECASE)
            if owner_match:
                owner = owner_match.group(1)
            
            # Look for dates
            for pattern in DATE_PATTERNS:
                date_match = re.search(pattern, sentence)
                if date_match:
                    due = date_match.group(0)
                    break
            
            action_items.append({
                'action': sentence.strip(),
                'owner': owner,
                'due': due
            })
    
    return action_items


def extract_decisions(text: str) -> List[Dict]:
    """Extract decisions from text (best-effort)."""
    decisions = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(kw in sentence_lower for kw in DECISION_KEYWORDS):
            owner = "Unassigned"
            date = "Not specified"
            
            # Look for decision maker
            owner_match = re.search(r'(?:approved by|decided by|by)\s*:?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', sentence, re.IGNORECASE)
            if owner_match:
                owner = owner_match.group(1)
            
            # Look for dates
            for pattern in DATE_PATTERNS:
                date_match = re.search(pattern, sentence)
                if date_match:
                    date = date_match.group(0)
                    break
            
            decisions.append({
                'decision': sentence.strip(),
                'owner': owner,
                'effective_date': date
            })
    
    return decisions


def find_pii(text: str) -> Dict[str, List[str]]:
    """Find PII (emails, phone numbers) in text."""
    emails = re.findall(EMAIL_PATTERN, text)
    phones = re.findall(PHONE_PATTERN, text)
    return {
        'emails': list(set(emails)),
        'phones': list(set(phones))
    }

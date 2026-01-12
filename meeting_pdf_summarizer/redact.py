"""PII redaction utilities."""
import re
from typing import Dict, List


EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
PHONE_PATTERN = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b|\b\(\d{3}\)\s?\d{3}[-.]?\d{4}\b'
SSN_PATTERN = r'\b\d{3}-\d{2}-\d{4}\b'
CREDIT_CARD_PATTERN = r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'


def redact_pii(text: str, redact_emails: bool = True, redact_phones: bool = True,
               redact_ssn: bool = True, redact_credit_cards: bool = True) -> str:
    """
    Redact PII from text.
    
    Args:
        text: Input text
        redact_emails: Redact email addresses
        redact_phones: Redact phone numbers
        redact_ssn: Redact SSNs
        redact_credit_cards: Redact credit card numbers
    
    Returns:
        Text with PII redacted
    """
    result = text
    
    if redact_emails:
        result = re.sub(EMAIL_PATTERN, '[EMAIL REDACTED]', result)
    
    if redact_phones:
        result = re.sub(PHONE_PATTERN, '[PHONE REDACTED]', result)
    
    if redact_ssn:
        result = re.sub(SSN_PATTERN, '[SSN REDACTED]', result)
    
    if redact_credit_cards:
        result = re.sub(CREDIT_CARD_PATTERN, '[CARD REDACTED]', result)
    
    return result

"""
Meeting retrieval service for Phi AI chat.
Searches and filters user meetings to provide context for LLM.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import re

# Paths
ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"
MEETINGS_JSON = OUTPUT_DIR / "meetings.json"


def load_meetings() -> List[Dict[str, Any]]:
    """Load all meetings from meetings.json"""
    if not MEETINGS_JSON.exists():
        return []
    try:
        return json.loads(MEETINGS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_user_meetings(user_email: str) -> List[Dict[str, Any]]:
    """Get all meetings where user is a participant"""
    meetings = load_meetings()
    user_meetings = []
    for meeting in meetings:
        participants = meeting.get("participants", [])
        if user_email.lower() in [p.lower() for p in participants]:
            user_meetings.append(meeting)
    return sorted(user_meetings, key=lambda x: x.get("processed_at", ""), reverse=True)


def parse_time_filter(query: str) -> Optional[Dict[str, Any]]:
    """
    Parse time-related keywords from query.
    Returns dict with date range or None.
    """
    query_lower = query.lower()
    now = datetime.now()
    
    if "today" in query_lower:
        return {"start": now.replace(hour=0, minute=0, second=0, microsecond=0)}
    elif "this week" in query_lower:
        start = now - timedelta(days=now.weekday())
        return {"start": start.replace(hour=0, minute=0, second=0, microsecond=0)}
    elif "last week" in query_lower:
        start = now - timedelta(days=now.weekday() + 7)
        end = now - timedelta(days=now.weekday())
        return {"start": start.replace(hour=0, minute=0, second=0, microsecond=0), "end": end.replace(hour=0, minute=0, second=0, microsecond=0)}
    elif "this month" in query_lower:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return {"start": start}
    elif "last month" in query_lower:
        if now.month == 1:
            start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return {"start": start, "end": end}
    elif "yesterday" in query_lower:
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"start": start, "end": end}
    
    return None


def filter_meetings_by_time(meetings: List[Dict[str, Any]], time_filter: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter meetings by time range"""
    if not time_filter:
        return meetings
    
    filtered = []
    for meeting in meetings:
        processed_at_str = meeting.get("processed_at", "")
        if not processed_at_str:
            continue
        
        try:
            processed_at = datetime.fromisoformat(processed_at_str.replace("Z", "+00:00"))
            if processed_at.tzinfo:
                processed_at = processed_at.replace(tzinfo=None)
        except Exception:
            continue
        
        if time_filter.get("start") and processed_at < time_filter["start"]:
            continue
        if time_filter.get("end") and processed_at >= time_filter["end"]:
            continue
        
        filtered.append(meeting)
    
    return filtered


def search_meetings_by_keywords(meetings: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """
    Intelligently search meetings by keywords in name, transcript, or participants.
    Returns meetings sorted by relevance with better scoring.
    """
    query_lower = query.lower()
    # Extract meaningful terms (longer words are more important)
    query_terms = [t.strip() for t in query_lower.split() if len(t.strip()) > 2]
    
    if not query_terms:
        return meetings
    
    scored_meetings = []
    
    for meeting in meetings:
        score = 0
        
        # Check meeting name (highest weight)
        name = meeting.get("name", "").lower()
        for term in query_terms:
            if term in name:
                # Exact match gets higher score
                if name == term or name.startswith(term) or name.endswith(term):
                    score += 20
                else:
                    score += 10
        
        # Check participants (medium weight)
        participants = meeting.get("participants", [])
        for participant in participants:
            participant_lower = participant.lower()
            for term in query_terms:
                if term in participant_lower:
                    score += 8
        
        # Check transcript (lower weight but more comprehensive)
        transcript_path = meeting.get("transcript_path")
        if transcript_path:
            try:
                transcript_file = ROOT / transcript_path
                if transcript_file.exists():
                    transcript_text = transcript_file.read_text(encoding="utf-8", errors="ignore").lower()
                    # Count occurrences and proximity
                    for term in query_terms:
                        count = transcript_text.count(term)
                        if count > 0:
                            # More occurrences = higher relevance
                            score += min(count, 10)  # Cap at 10 points per term
            except Exception:
                pass
        
        # Boost score for recent meetings if query is about "recent" or "latest"
        if any(word in query_lower for word in ["recent", "latest", "last", "newest"]):
            processed_at = meeting.get("processed_at", "")
            if processed_at:
                try:
                    dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
                    days_ago = (datetime.now() - dt.replace(tzinfo=None)).days
                    if days_ago < 7:
                        score += 5
                    elif days_ago < 30:
                        score += 2
                except Exception:
                    pass
        
        # Always include meetings with any match
        if score > 0:
            scored_meetings.append((score, meeting))
    
    # Sort by score (highest first), then by date (newest first) for tie-breaking
    scored_meetings.sort(key=lambda x: (
        x[0],  # Score first
        x[1].get("processed_at", "")  # Then date
    ), reverse=True)
    
    return [m for _, m in scored_meetings]


def extract_meeting_context(meeting: Dict[str, Any], max_chars: int = 2000, query: Optional[str] = None) -> str:
    """
    Intelligently extract relevant context from a meeting.
    If a query is provided, tries to find the most relevant sections of the transcript.
    """
    context_parts = []
    
    # Meeting name
    name = meeting.get("name", "Unnamed Meeting")
    context_parts.append(f"Meeting: {name}")
    
    # Date
    processed_at = meeting.get("processed_at", "")
    if processed_at:
        try:
            dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
            context_parts.append(f"Date: {dt.strftime('%B %d, %Y at %I:%M %p')}")
        except Exception:
            context_parts.append(f"Date: {processed_at}")
    
    # Participants
    participants = meeting.get("participants", [])
    if participants:
        context_parts.append(f"Participants: {', '.join(participants)}")
    
    # Transcript excerpt - smarter extraction
    transcript_path = meeting.get("transcript_path")
    if transcript_path:
        try:
            transcript_file = ROOT / transcript_path
            if transcript_file.exists():
                transcript_text = transcript_file.read_text(encoding="utf-8", errors="ignore")
                
                # If query provided, try to find relevant sections
                if query and len(query.split()) > 2:
                    query_terms = [t.lower() for t in query.split() if len(t) > 3]
                    if query_terms:
                        # Find sentences/paragraphs containing query terms
                        lines = transcript_text.split('\n')
                        relevant_lines = []
                        for line in lines:
                            line_lower = line.lower()
                            if any(term in line_lower for term in query_terms):
                                relevant_lines.append(line)
                        
                        if relevant_lines:
                            # Take relevant sections, up to max_chars
                            excerpt = '\n'.join(relevant_lines[:50])  # Limit to 50 lines
                            if len(excerpt) > max_chars:
                                excerpt = excerpt[:max_chars] + "..."
                            context_parts.append(f"Relevant transcript sections:\n{excerpt}")
                        else:
                            # Fallback to beginning
                            excerpt = transcript_text[:max_chars]
                            if len(transcript_text) > max_chars:
                                excerpt += "..."
                            context_parts.append(f"Transcript excerpt:\n{excerpt}")
                    else:
                        # No meaningful query terms, use beginning
                        excerpt = transcript_text[:max_chars]
                        if len(transcript_text) > max_chars:
                            excerpt += "..."
                        context_parts.append(f"Transcript excerpt:\n{excerpt}")
                else:
                    # No query, use beginning of transcript
                    excerpt = transcript_text[:max_chars]
                    if len(transcript_text) > max_chars:
                        excerpt += "..."
                    context_parts.append(f"Transcript excerpt:\n{excerpt}")
        except Exception:
            pass
    
    return "\n\n".join(context_parts)


def retrieve_meeting_context_smart(
    user_email: str,
    query: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    max_meetings: int = 15,
    max_chars_per_meeting: int = 2500
) -> str:
    """
    Intelligently retrieve meeting context based on query and conversation history.
    This version is smarter about what meetings to include based on the conversation flow.
    """
    # Get user's meetings
    user_meetings = get_user_meetings(user_email)
    
    if not user_meetings:
        return "No meetings found for this user."
    
    # Extract topics and keywords from conversation
    all_text = query.lower()
    if conversation_history:
        for msg in conversation_history:
            if msg.get("role") == "user":
                all_text += " " + msg.get("content", "").lower()
    
    # Apply time filter if present
    time_filter = parse_time_filter(query)
    if time_filter:
        user_meetings = filter_meetings_by_time(user_meetings, time_filter)
    
    # Search by keywords (more intelligent search)
    if len(query.split()) > 0:
        user_meetings = search_meetings_by_keywords(user_meetings, all_text)
    
    # Prioritize recent meetings if no specific time filter
    if not time_filter:
        user_meetings = sorted(user_meetings, key=lambda x: x.get("processed_at", ""), reverse=True)
    
    # Limit number of meetings
    user_meetings = user_meetings[:max_meetings]
    
    if not user_meetings:
        return "No meetings match your query. Try asking about a different time period or topic."
    
    # Build context with better formatting
    context_parts = [
        f"You have access to {len(user_meetings)} relevant meeting(s) from the user's history. Use this information to answer their question naturally and conversationally:",
        ""
    ]
    
    for i, meeting in enumerate(user_meetings, 1):
        # Pass query to extract_meeting_context for smarter excerpt selection
        meeting_context = extract_meeting_context(meeting, max_chars_per_meeting, query=query)
        context_parts.append(f"--- Meeting {i} ---")
        context_parts.append(meeting_context)
        context_parts.append("")
    
    return "\n".join(context_parts)


def retrieve_meeting_context(
    user_email: str,
    query: str,
    max_meetings: int = 10,
    max_chars_per_meeting: int = 2000
) -> str:
    """
    Retrieve relevant meeting context for a user query.
    Returns formatted context string for LLM.
    """
    # Get user's meetings
    user_meetings = get_user_meetings(user_email)
    
    if not user_meetings:
        return "No meetings found for this user."
    
    # Apply time filter if present
    time_filter = parse_time_filter(query)
    if time_filter:
        user_meetings = filter_meetings_by_time(user_meetings, time_filter)
    
    # Search by keywords
    if len(query.split()) > 1:  # Only search if query has multiple words
        user_meetings = search_meetings_by_keywords(user_meetings, query)
    
    # Limit number of meetings
    user_meetings = user_meetings[:max_meetings]
    
    if not user_meetings:
        return "No meetings match your query."
    
    # Build context
    context_parts = [
        f"You have access to {len(user_meetings)} relevant meeting(s). Use this information to answer the user's question:",
        ""
    ]
    
    for i, meeting in enumerate(user_meetings, 1):
        # Pass query to extract_meeting_context for smarter excerpt selection
        meeting_context = extract_meeting_context(meeting, max_chars_per_meeting, query=query)
        context_parts.append(f"--- Meeting {i} ---")
        context_parts.append(meeting_context)
        context_parts.append("")
    
    return "\n".join(context_parts)

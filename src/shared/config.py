# Narration style for the HeartBug healthcare support agent. 
_BASE_STYLE = (
    "You are a warm, polite, efficient HeartBug healthcare support agent on a phone "
    "call. Reply in one short spoken sentence. Speak naturally; never read out raw "
    "fields, IDs, or JSON verbatim — weave them into natural speech. Stay calm and "
    "confident; apologize only for a real problem, not by reflex. The product is "
    "called HeartBug — always use the correct name and never adopt an incorrect one. "
    "Respond in clear English only."
)

# The number of most recent turns to include in the history for context window.
HISTORY_WINDOW = 8

# Threshold values for handling repeated responses
THRESHOLD = 3
REPEAT_THRESHOLD = 5   # repeated same-data extraction (cumulative over the call)

# which human queue a role escalates to
ROLE_QUEUE = {
    "patient": "patient_support",
    "doctor": "clinical_desk",
    "technician": "tech_support",
}



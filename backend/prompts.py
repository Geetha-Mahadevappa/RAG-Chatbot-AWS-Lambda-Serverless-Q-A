"""
Versioned prompt templates.

The point of versioning prompts explicitly (rather than editing an inline f-string in
rag_core.py) is traceability: every response logs which PROMPT_VERSION + template name
produced it, so if answer quality shifts, you can correlate the shift with a specific prompt
change instead of guessing. Bump PROMPT_VERSION whenever any template's wording changes.
"""

PROMPT_VERSION = "v1"

# Used when retrieval found chunks with confident relevance (see guardrails.relevance_tier).
GROUNDED_TEMPLATE = (
    "Answer the question using ONLY the context below. "
    "If the context does not contain the answer, say you don't know.\n\n"
    "Context:\n{context}\n\n"
    "Question: {query}\n"
    "Answer:"
)

# Used when retrieval found chunks, but only in the "weak" relevance band (see
# guardrails.relevance_tier) -- present but marginal. Explicitly instructs the model to
# prefer admitting uncertainty over confidently answering from a shaky match.
WEAK_CONTEXT_TEMPLATE = (
    "The context below may only be loosely related to the question -- it was retrieved with "
    "low confidence. Read it carefully. If it does not actually answer the question, say "
    "plainly that you don't have enough information, rather than guessing.\n\n"
    "Context:\n{context}\n\n"
    "Question: {query}\n"
    "Answer:"
)

# Used when the user marked a previous answer as unsatisfactory and the frontend triggered one
# automatic regeneration (capped at config.MAX_REGENERATIONS -- see lambda_handler.py). Giving
# the model its own previous answer and an explicit "do better" instruction, rather than just
# re-running the same prompt, is what actually gives regeneration a chance of being different
# -- re-sending an identical prompt to the same model would likely reproduce the same answer.
SELF_CORRECTION_TEMPLATE = (
    "You previously answered this question, but the user was not satisfied with the answer.\n\n"
    "Context:\n{context}\n\n"
    "Question: {query}\n\n"
    "Previous answer: {previous_answer}\n\n"
    "Using ONLY the context above, provide a better, more thorough or differently-angled "
    "answer. If the context genuinely does not contain enough information to improve on the "
    "previous answer, say so honestly rather than inventing detail.\n"
    "Improved answer:"
)

TEMPLATES = {
    "grounded": GROUNDED_TEMPLATE,
    "weak_context": WEAK_CONTEXT_TEMPLATE,
    "self_correction": SELF_CORRECTION_TEMPLATE,
}


def build_prompt(template_name: str, query: str, context: str, **extra) -> str:
    return TEMPLATES[template_name].format(context=context, query=query, **extra)


def graceful_degradation_message(query: str) -> str:
    """Used for the out_of_scope rejection -- no relevant chunks exist at all, so we never
    call the (billed) SageMaker endpoint, but that's no reason for the user-facing message to
    read like a bare error code. Deliberately a static template, not LLM-generated -- keeping
    the reject path free is the whole point of rejecting before the generation call."""
    return (
        f'I don\'t have information about "{query}" in my knowledge base yet. '
        "I can help with questions about AWS Lambda basics, Function URLs, and Lambda "
        "pricing -- try rephrasing your question around one of those topics."
    )

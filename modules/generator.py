"""
Comment generation engine.
Generates 4 comment options per post using Groq (primary) or Gemini (fallback).

Options:
  A — Challenge : challenges a key claim with technical backing + provocation
  B — Expand    : adds something the original genuinely missed
  C — Nuanced   : "yes, and..." validates but adds an important caveat/edge case
  D — Question  : expert question directed at author, invites reply

Each prompt includes top existing replies so the LLM never duplicates them.
"""

import logging
import os
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Shared context builder ────────────────────────────────────────────────────

def _build_context(post: dict) -> dict:
    replies = post.get("top_replies", [])
    if replies:
        existing = "\n".join(
            f'  - @{r["handle"]}: "{r["text"][:120]}" ({r["likes"]} likes)'
            for r in replies[:3]
        )
        existing_block = f"\nEXISTING TOP REPLIES (DO NOT repeat these angles):\n{existing}\n"
    else:
        existing_block = ""

    return {
        "post_text":       post.get("text", ""),
        "author_handle":   post.get("author_handle", "unknown"),
        "author_followers": _fmt(post.get("author_followers", 0)),
        "views":           _fmt(post.get("views", 0)),
        "likes":           _fmt(post.get("likes", 0)),
        "existing_block":  existing_block,
    }


def _fmt(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


# ── Prompt templates ──────────────────────────────────────────────────────────

CHALLENGE_PROMPT = """\
You are Mo (@mohanp_ai), AI engineer specializing in post-training, agentic AI, and ML systems.

POST by @{author_handle} ({author_followers} followers, {views} views, {likes} likes):
{post_text}
{existing_block}
Write a CHALLENGE comment. Strategy:
1. Find the most debatable claim or assumption — push back on it with technical evidence
2. Back it with a specific mechanism, paper finding, or real-world counterexample
3. End with one sharp provocative question

STRICT rules: 200–280 characters (use the full space — be dense and specific) · no "Great post" · no generic openers · no hashtags
Output ONLY the comment text:"""

EXPAND_PROMPT = """\
You are Mo (@mohanp_ai), AI engineer specializing in post-training, agentic AI, and ML systems.

POST by @{author_handle} ({author_followers} followers, {views} views, {likes} likes):
{post_text}
{existing_block}
Write an EXPAND comment. Strategy:
1. Identify the most important thing the post left out or didn't consider
2. Add that insight with technical precision — a new angle, data point, or implication
3. Don't just agree — bring something genuinely new

STRICT rules: 200–280 characters (use the full space — be dense and specific) · no "Great post" · no generic openers · no hashtags
Output ONLY the comment text:"""

NUANCED_PROMPT = """\
You are Mo (@mohanp_ai), AI engineer specializing in post-training, agentic AI, and ML systems.

POST by @{author_handle} ({author_followers} followers, {views} views, {likes} likes):
{post_text}
{existing_block}
Write a NUANCED comment. Strategy:
1. Validate the core insight — show you understood it deeply
2. Add an important caveat, edge case, or condition where it breaks down or changes
3. Format: "True, but only when X. In Y scenario, [different outcome]."

STRICT rules: 200–280 characters (use the full space — be dense and specific) · no "Great post" · no generic openers · no hashtags
Output ONLY the comment text:"""

QUESTION_PROMPT = """\
You are Mo (@mohanp_ai), AI engineer specializing in post-training, agentic AI, and ML systems.

POST by @{author_handle} ({author_followers} followers, {views} views, {likes} likes):
{post_text}
{existing_block}
Write a QUESTION comment. Strategy:
1. Ask the author ONE specific expert question that shows you understand the deep mechanics
2. The question should be something only someone who works in this area would ask
3. Keep it directed at the author — make them want to reply

STRICT rules: 200–280 characters (use the full space — be dense and specific) · one question only · no "Great post" · no hashtags
Output ONLY the comment text:"""

PROMPTS = {
    "challenge": CHALLENGE_PROMPT,
    "expand":    EXPAND_PROMPT,
    "nuanced":   NUANCED_PROMPT,
    "question":  QUESTION_PROMPT,
}

# ── Domain terms for quality validation ──────────────────────────────────────

DOMAIN_TERMS = [
    "RLHF", "DPO", "PPO", "distillation", "agent", "reasoning", "post-training",
    "alignment", "tool use", "orchestration", "memory", "RAG", "embeddings",
    "context", "inference", "fine-tuning", "transformer", "attention", "token",
    "model", "training", "dataset", "benchmark", "evaluation", "prompt",
    "dashboard", "SQL", "analytics", "pipeline", "vector", "retrieval",
    "markdown", "parsing", "chunking", "LLM", "API", "latency", "throughput",
    "accuracy", "precision", "recall", "loss", "gradient", "weight",
]

GENERIC_OPENERS = [
    "great post", "thanks for sharing", "interesting perspective",
    "i agree with", "this is important", "well said", "totally agree",
    "love this", "so true", "100%",
]


# ── LLM clients ───────────────────────────────────────────────────────────────

def _call_groq(prompt: str, model: str, temperature: float, max_tokens: int) -> str:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def _call_gemini(prompt: str, model: str, temperature: float, max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    m = genai.GenerativeModel(model)
    response = m.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text.strip()


def _call_llm(prompt: str, config: dict) -> str:
    cfg         = config.get("comments", {})
    provider    = cfg.get("llm_provider", "groq")
    temperature = float(cfg.get("temperature", 0.8))
    max_tokens  = int(cfg.get("max_tokens", 200))

    if provider == "groq":
        model = cfg.get("groq_model", "llama-3.3-70b-versatile")
        try:
            return _call_groq(prompt, model, temperature, max_tokens)
        except Exception as e:
            logger.warning(f"Groq failed ({e}), falling back to Gemini")
            return _call_gemini(prompt, cfg.get("gemini_model", "gemini-2.0-flash"), temperature, max_tokens)
    else:
        model = cfg.get("gemini_model", "gemini-2.0-flash")
        try:
            return _call_gemini(prompt, model, temperature, max_tokens)
        except Exception as e:
            logger.warning(f"Gemini failed ({e}), falling back to Groq")
            return _call_groq(prompt, cfg.get("groq_model", "llama-3.3-70b-versatile"), temperature, max_tokens)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_comment(comment: str) -> Tuple[bool, list]:
    issues = []
    if len(comment) < 150:
        issues.append(f"Too short ({len(comment)} chars — target 200–280)")
    if len(comment) > 280:
        issues.append(f"Over Twitter limit ({len(comment)}/280 chars)")
    for phrase in GENERIC_OPENERS:
        if phrase in comment.lower():
            issues.append(f"Generic opener: '{phrase}'")
            break
    if "afterburn" in comment.lower():
        low = comment.lower()
        if "check out afterburn" in low or "try afterburn" in low:
            issues.append("Forced library mention")
    has_depth = any(t.lower() in comment.lower() for t in DOMAIN_TERMS)
    if not has_depth:
        issues.append("No technical depth (no domain terms)")
    return len(issues) == 0, issues


# ── Main generator ────────────────────────────────────────────────────────────

def generate_comments(post: dict, config: dict) -> dict:
    """
    Generate 4 comment options for a post.
    Returns dict:
      {
        "challenge": (text, issues),
        "expand":    (text, issues),
        "nuanced":   (text, issues),
        "question":  (text, issues),
      }
    """
    ctx = _build_context(post)
    results = {}

    for tone, prompt_tpl in PROMPTS.items():
        prompt = prompt_tpl.format(**ctx)
        try:
            text = _call_llm(prompt, config)
            valid, issues = validate_comment(text)
            if not valid:
                logger.warning(f"  [{tone}] issues: {issues}")
            results[tone] = (text, issues)
            logger.info(f"  [{tone}] {len(text)}c: {text[:60]}...")
        except Exception as e:
            logger.error(f"  [{tone}] generation failed: {e}")
            results[tone] = ("", [f"Generation failed: {e}"])

    return results

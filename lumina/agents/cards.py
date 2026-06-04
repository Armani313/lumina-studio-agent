"""Card production: marketplace product-card variants (image + composited crisp text)."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.cards import make_product_card

card_production_agent = LlmAgent(
    name="card_production",
    model=settings.model_reasoning,
    description="Produces marketplace product-card variants (on-brand image + crisp composited text).",
    instruction=(
        "You design high-converting marketplace product cards. Using the brief, plan and copy "
        "below, call make_product_card TWICE for 2 distinct, SELLING card variants (e.g. one "
        "benefit-led, one emotional/hero). For each pass provide: a punchy headline (<=42 chars), "
        "one supporting subtext line (<=90 chars), 4 short benefit bullets, a short call-to-action "
        "(cta, <=20 chars, e.g. 'Shop now'), the brand wordmark (brand_name from the brief), and a "
        "background scene description that leaves clean negative space in the lower third for text. "
        "Make the wording persuasive; write headline, subtext, bullets and cta in the brief's "
        "language (the user's language). Then reply with a JSON array of the produced card gs_uris.\n\n"
        "Brief:\n{brief}\n\nPlan:\n{plan}\n\nCopy (reuse the strongest angles):\n{copy_doc?}"
    ),
    tools=[make_product_card],
    output_key="cards",
)

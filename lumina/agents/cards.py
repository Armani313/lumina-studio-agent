"""Card production: marketplace product-card variants (image + composited crisp text)."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.cards import make_product_card

card_production_agent = LlmAgent(
    name="card_production",
    model=settings.model_reasoning,
    description="Produces marketplace product-card variants (image model designs the whole card).",
    instruction=(
        "You art-direct high-converting marketplace product cards. The image model designs the WHOLE "
        "card (product hero + typography + layout + palette), automatically matched to the product's "
        "identity and audience — so keep the ON-CARD text SHORT and punchy. Using the brief, plan and "
        "copy below, call make_product_card once per card variant — make EXACTLY the production spec's "
        "card_count cards (default 2 if no spec), passing aspect_ratio = the spec's card_aspect_ratio "
        "(default '4:5'). Make the variants distinct and SELLING (e.g. one benefit-led, one "
        "emotional/hero). For each pass provide: a punchy headline (<=42 chars), one "
        "short supporting subtext line (<=90 chars), 3-4 short benefit bullets, a short call-to-action "
        "(cta, <=20 chars, e.g. 'Shop now'), the brand wordmark (brand_name from the brief), and a "
        "background scene description that suits the product. Reflect the brand's palette and visual "
        "style from the brand research below in your scene description and tone. Write headline, "
        "subtext, bullets and cta in the brief's language (the user's language); make the wording "
        "persuasive. Then reply with a JSON array of the produced card gs_uris.\n\n"
        "Production spec (honor card_count, card_aspect_ratio):\n{spec?}\n\n"
        "Brief:\n{brief}\n\nBrand research (palette/style to honor):\n{brand_research?}\n\n"
        "Plan:\n{plan}\n\nCopy (reuse the strongest angles):\n{copy_full?}"
    ),
    tools=[make_product_card],
    output_key="cards",
)

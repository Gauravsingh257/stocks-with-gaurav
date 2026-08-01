"""
services/instagram_carousel — Automated Instagram carousel generation.

Pipeline: Signal → Action Tag → Claude Content → Bannerbear Images → URLs
"""

from services.instagram_carousel.pipeline import generate_signal_carousel, generate_bulk_carousels
from services.instagram_carousel.action_logic import determine_action_tag

__all__ = ["generate_signal_carousel", "generate_bulk_carousels", "determine_action_tag"]

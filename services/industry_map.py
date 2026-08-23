"""
services/industry_map.py — provider INDUSTRY -> canonical sector.

The provider's `sector` field is far too coarse for an Indian equity book:
"Basic Materials" lumps a specialty-chemicals maker with a steel roller, and
"Industrials" swallows engineering, logistics and capital goods alike. The
`industry` field is one level finer and maps cleanly onto the sectors the engine
already reasons about, so it is preferred wherever present.

Anything unrecognised falls back to the coarse sector map, and then to
"Unassigned" — which is a fully eligible bucket, never a rejection.
"""

from __future__ import annotations

# Finer provider industry -> canonical engine sector.
INDUSTRY_TO_CANON: dict[str, str] = {
    # Chemicals
    "Specialty Chemicals": "Chemicals", "Chemicals": "Chemicals",
    "Agricultural Inputs": "Chemicals",
    # Textiles / apparel
    "Textile Manufacturing": "Textiles", "Apparel Manufacturing": "Textiles",
    "Apparel Retail": "Consumer Services", "Footwear & Accessories": "Consumer Services",
    "Luxury Goods": "Consumer Services",
    # Metals & mining
    "Steel": "Metal", "Metal Fabrication": "Metal",
    "Other Industrial Metals & Mining": "Metal", "Aluminum": "Metal",
    "Copper": "Metal", "Gold": "Metal",
    # Construction / infra / materials
    "Engineering & Construction": "Infra", "Infrastructure Operations": "Infra",
    "Building Products & Equipment": "Capital Goods", "Building Materials": "Cement",
    # Real estate
    "Real Estate - Development": "Realty", "Real Estate Services": "Realty",
    "Real Estate - Diversified": "Realty",
    # Auto
    "Auto Parts": "Auto", "Auto Manufacturers": "Auto",
    "Auto & Truck Dealerships": "Auto", "Recreational Vehicles": "Auto",
    # Financials
    "Capital Markets": "Finance", "Asset Management": "Finance",
    "Credit Services": "Finance", "Banks - Regional": "Finance",
    "Banks - Diversified": "Finance", "Insurance - Life": "Finance",
    "Financial Data & Stock Exchanges": "Finance", "Mortgage Finance": "Finance",
    # Capital goods / industrials
    "Specialty Industrial Machinery": "Capital Goods",
    "Electrical Equipment & Parts": "Capital Goods",
    "Farm & Heavy Construction Machinery": "Capital Goods",
    "Tools & Accessories": "Capital Goods",
    "Industrial Distribution": "Capital Goods",
    "Business Equipment & Supplies": "Capital Goods",
    "Pollution & Treatment Controls": "Capital Goods",
    "Electronic Components": "Capital Goods",
    "Aerospace & Defense": "Capital Goods",
    # IT
    "Information Technology Services": "IT", "Software - Application": "IT",
    "Software - Infrastructure": "IT", "Consulting Services": "IT",
    "Electronics & Computer Distribution": "IT",
    "Internet Content & Information": "IT",
    "Semiconductors": "IT", "Computer Hardware": "IT",
    # Telecom
    "Telecom Services": "Telecom", "Communication Equipment": "Telecom",
    # Healthcare
    "Drug Manufacturers - Specialty & Generic": "Pharma",
    "Drug Manufacturers - General": "Pharma", "Biotechnology": "Pharma",
    "Medical Care Facilities": "Pharma", "Medical Devices": "Pharma",
    "Medical Instruments & Supplies": "Pharma", "Diagnostics & Research": "Pharma",
    "Pharmaceutical Retailers": "Pharma", "Health Information Services": "Pharma",
    # Consumer staples
    "Packaged Foods": "FMCG", "Confectioners": "FMCG", "Farm Products": "FMCG",
    "Food Distribution": "FMCG", "Beverages - Non-Alcoholic": "FMCG",
    "Beverages - Wineries & Distilleries": "FMCG", "Tobacco": "FMCG",
    "Household & Personal Products": "FMCG",
    # Consumer discretionary / services
    "Restaurants": "Consumer Services", "Lodging": "Consumer Services",
    "Resorts & Casinos": "Consumer Services", "Travel Services": "Consumer Services",
    "Department Stores": "Consumer Services", "Specialty Retail": "Consumer Services",
    "Education & Training Services": "Consumer Services",
    "Personal Services": "Consumer Services",
    "Furnishings, Fixtures & Appliances": "Consumer Durables",
    "Consumer Electronics": "Consumer Durables",
    # Media
    "Entertainment": "Media", "Advertising Agencies": "Media",
    "Publishing": "Media", "Broadcasting": "Media",
    # Services / logistics
    "Integrated Freight & Logistics": "Services", "Trucking": "Services",
    "Marine Shipping": "Services", "Airlines": "Services",
    "Waste Management": "Services", "Specialty Business Services": "Services",
    "Staffing & Employment Services": "Services",
    "Rental & Leasing Services": "Services", "Security & Protection Services": "Services",
    # Energy & utilities
    "Oil & Gas Refining & Marketing": "Energy", "Oil & Gas Equipment & Services": "Energy",
    "Oil & Gas Integrated": "Energy", "Oil & Gas E&P": "Energy",
    "Oil & Gas Midstream": "Energy", "Thermal Coal": "Energy",
    "Solar": "Energy", "Uranium": "Energy",
    "Utilities - Regulated Electric": "Power",
    "Utilities - Independent Power Producers": "Power",
    "Utilities - Renewable": "Power", "Utilities - Regulated Gas": "Utilities",
    # Paper & packaging
    "Paper & Paper Products": "Forest Materials",
    "Packaging & Containers": "Forest Materials",
    "Lumber & Wood Production": "Forest Materials",
    # Catch-all
    "Conglomerates": "Diversified",
}

# Coarse provider sector -> canonical, used only when industry is unmapped.
SECTOR_TO_CANON: dict[str, str] = {
    "Financial Services": "Finance", "Technology": "IT", "Healthcare": "Pharma",
    "Consumer Cyclical": "Consumer Services", "Consumer Defensive": "FMCG",
    "Basic Materials": "Chemicals", "Energy": "Energy", "Industrials": "Capital Goods",
    "Real Estate": "Realty", "Utilities": "Utilities", "Communication Services": "Telecom",
}


def canon_from_provider(industry: str | None, sector: str | None) -> str | None:
    """Best canonical sector from a provider row. None when nothing maps."""
    if industry:
        hit = INDUSTRY_TO_CANON.get(industry.strip())
        if hit:
            return hit
    if sector:
        hit = SECTOR_TO_CANON.get(sector.strip())
        if hit:
            return hit
    return None

"""
dashboard/backend/routes/__init__.py
Router registry — imported by main.py to include all route modules.
"""

from .trades            import router as trades_router
from .analytics         import router as analytics_router
from .journal           import router as journal_router
from .agents            import router as agents_router
from .charts            import router as charts_router
from .chat              import router as chat_router
from .system            import router as system_router
from .oi_intelligence   import router as oi_intelligence_router
from .engine_router     import router as engine_router  # Phase 7: decision trace API
from .research          import router as research_router

__all__ = ["trades_router", "analytics_router", "journal_router", "agents_router",
           "charts_router", "chat_router", "system_router", "oi_intelligence_router",
           "engine_router", "research_router"]

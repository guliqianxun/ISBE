"""Backwards-compat shim — nowcasting digester now goes through the generic flow.

Kept so existing imports / tests / CLI dispatch keep working.
"""

from isbe.topics._shared.digester import (
    parse_distillation_section,  # noqa: F401  — re-exported for tests
)
from isbe.topics._shared.digester import (
    weekly_digester as _generic_weekly_digester,
)


def weekly_digester(period_label=None, today=None):
    """Run the generic digester for the nowcasting topic."""
    return _generic_weekly_digester(
        topic_id="nowcasting", period_label=period_label, today=today
    )

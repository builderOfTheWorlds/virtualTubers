"""
office — the ashiorid_office show layer (see .claude/prompts/ashiorid_office_build_plan.md).

Kept deliberately minimal: submodules (roles, protocol, clock, ...) are
imported directly by callers, e.g. ``from office.roles import OfficeRole``.
Nothing is re-exported here so that parallel work packages never collide on
this file and importing ``office`` never drags in optional dependencies.
"""

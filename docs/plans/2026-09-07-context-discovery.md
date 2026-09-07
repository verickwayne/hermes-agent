# Bounded native skill discovery

`skills.prompt_max_chars` defaults to 6,000. A larger startup skill index becomes
a short discovery guide when both `skills_list` and `skill_view` are available.
Set the value to zero for the complete index. Smaller indexes remain unchanged.
The threshold participates in the process cache key; the agent still builds a
stable session prompt, without rewriting prior messages or tool definitions.

`skills_list(query="task keywords")` searches names, descriptions and categories.
Omit query to browse, optionally filter category, and follow `next_offset` for
subsequent pages. Default limit is ten, maximum fifty, and serialized output is
bounded to 6,000 characters. `count` counts this page; `total` counts matches.
Descriptions are shortened only in the discovery result. `skill_view` retains
the complete original skill and linked resources. Native disabled/platform
filtering and external-directory precedence are retained.

Installed catalog measurement: the skill prompt fell from 11,135 to 876
characters, and the first listing from 13,542 to 1,619 characters, with all
89 skills reachable by pagination. These are character counts for the skill
blocks, not total-session token estimates.

Focused prompt, skill utility and tool tests: 215 passed, one skipped. A native
CLI conversation against an isolated local fixture provider discovered the
requested skill and completed normally without including the 100 fixture
descriptions in its initial request.

The canonical full suite was run: 22,975 passed, 56 failed, 159 skipped, and
three setup errors. This is not a clean full-suite result. Of the failures,
33 reproduce on the unchanged base commit and on this branch with identical
test identities (macOS/systemd assumptions, an unavailable security-helper
download, live provider discovery, and existing temporary-path guards).
Thirteen others pass when rerun on this branch: eleven shell timeouts and two
order/timing-sensitive tests; the two latter tests and a representative shell
test also pass separately on the baseline. Ten SSH failures and three SSH
setup errors require an unreachable external test host and a key absent from
this machine. No security guards were disabled to change these results.

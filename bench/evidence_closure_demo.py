"""验证条件规划与手工路由规则的同预算结果。"""

from bench.evidence_closure_planner import ClosurePlanner, Reading, Scenario, Source
from bench.evidence_frontier import Context, Decision, Evidence, Kind, decide

def compare() -> dict[str, tuple[int, int]]:
    sources = (Source("route", 1), Source("a", 2, True), Source("b", 2, True))
    worlds = (
        Scenario(1, (("route", "route_a"), ("a", "commit"))),
        Scenario(1, (("route", "route_a"),)),
        Scenario(1, (("route", "route_b"), ("b", "commit"))),
        Scenario(1, (("route", "route_b"),)),
    )
    context = Context("intent", "zone", "digest", "provider", 1, 0, False, False)
    proof = Evidence(Kind.COMMITTED, "intent", "zone", "digest", "provider", 1, 10,
                     authenticated=True, final=True)
    planner = ClosurePlanner(sources, worlds)
    totals = {name: [0, 0] for name in ("planner", "fixed_a", "fixed_b", "rule_route")}
    for world in worlds:
        def fetch(name):
            kind = world.reply(name)
            return Reading(kind, proof if kind == "commit" else None)
        for policy in totals:
            if policy == "planner":
                result = planner.execute(context, 3, fetch)
                success, cost = result.decision == Decision.CONFIRM, result.total_cost
            else:
                if policy == "fixed_a":
                    names = ("a",)
                elif policy == "fixed_b":
                    names = ("b",)
                else:
                    names = ("route", "a" if fetch("route").kind == "route_a" else "b")
                cost = sum(s.cost for s in sources if s.name in names)
                last = fetch(names[-1])
                success = last.kind == "commit" and last.evidence is not None and (
                    decide(context, (last.evidence,)).action == Decision.CONFIRM)
            totals[policy][0] += int(success)
            totals[policy][1] += cost
    return {name: tuple(value) for name, value in totals.items()}


if __name__ == "__main__":
    print(compare())

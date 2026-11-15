"""Pure graph algebra. Semantic scope is supplied, never guessed by this module."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Requirements:
    inventory: frozenset[str]
    roots: frozenset[str]
    requires: frozenset[tuple[str, str]]

    @classmethod
    def parse(cls, raw, inventory):
        if not isinstance(raw, dict) or set(raw) != {'roots', 'requires'}:
            raise ValueError('Expected roots and requires')
        roots, edges = raw['roots'], raw['requires']
        if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
            raise ValueError('roots must be IDs')
        if not isinstance(edges, list) or any(
            not isinstance(e, list) or len(e) != 2
            or not all(isinstance(x, str) for x in e) for e in edges
        ):
            raise ValueError('requires must contain [consumer, prerequisite] pairs')
        graph = cls(frozenset(inventory), frozenset(roots),
                    frozenset(tuple(e) for e in edges))
        if not graph.roots <= graph.inventory or any(
            a not in graph.inventory or b not in graph.inventory or a == b
            for a, b in graph.requires
        ):
            raise ValueError('Unknown ID or self dependency')
        # Cycles can be meaningful in other graph models, but are outside this
        # experiment's dependency DAG. Do not silently accept a malformed scope.
        for origin in graph.inventory:
            reached, todo = set(), [b for a, b in graph.requires if a == origin]
            while todo:
                node = todo.pop()
                if node == origin:
                    raise ValueError('Cyclic dependency')
                if node not in reached:
                    reached.add(node)
                    todo.extend(b for a, b in graph.requires if a == node)
        return graph

    def live(self):
        reached = set(self.roots)
        while True:
            more = {b for a, b in self.requires if a in reached} - reached
            if not more:
                return frozenset(reached)
            reached.update(more)

    def json(self):
        active = self.live()
        return {'roots': sorted(self.roots),
                'requires': [list(e) for e in sorted(self.requires)
                             if set(e) <= active]}

    def withdraw(self, targets):
        """Withdraw roots; retain all other roots and their exact dependencies.

        Repeating a previous withdrawal is harmless. Withdrawing an auxiliary
        prerequisite or a root still required by another root needs a separate
        semantic decision, so it is explicitly unsupported, not cascaded.
        """
        targets = frozenset(targets)
        if not targets <= self.inventory:
            raise ValueError('Unknown withdrawal target')
        if (targets - self.roots) & self.live():
            raise ValueError('Cannot withdraw an auxiliary prerequisite as a root')
        candidate = Requirements(self.inventory, self.roots - targets, self.requires)
        if targets & candidate.live():
            raise ValueError('A surviving requirement still needs the withdrawn root')
        active = candidate.live()
        return Requirements(self.inventory, candidate.roots,
                            frozenset(e for e in candidate.requires if set(e) <= active))

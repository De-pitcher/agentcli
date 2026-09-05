"""Inter-Project Dependency Graph & Topological Engine (Phase 25).

Builds a Directed Acyclic Graph (DAG) of cross-workspace relationships,
detects circular dependencies, computes downstream change impact sets,
and provides topological ordering for multi-project workflows.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import WorkspaceRegistry

logger = logging.getLogger(__name__)


class DependencyCycleError(Exception):
    """Raised when circular dependencies are detected in the workspace mesh."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        chain = " -> ".join(cycle)
        super().__init__(f"Circular workspace dependency detected: {chain}")


class ProjectDependencyGraph:
    """Directed dependency graph across registered monorepo workspaces."""

    def __init__(self, registry: WorkspaceRegistry | None = None) -> None:
        self._registry = registry
        # Adjacency lists: node -> set of direct dependencies (outbound edges)
        self._deps: dict[str, set[str]] = defaultdict(set)
        # Reverse adjacency: node -> set of nodes depending directly on this node (inbound edges)
        self._dependents: dict[str, set[str]] = defaultdict(set)
        # Set of all known nodes
        self._nodes: set[str] = set()

        if registry is not None:
            self.rebuild()

    def add_node(self, name: str) -> None:
        """Add a workspace node to the dependency graph."""
        self._nodes.add(name)
        if name not in self._deps:
            self._deps[name] = set()
        if name not in self._dependents:
            self._dependents[name] = set()

    def add_dependency(self, from_ws: str, to_ws: str) -> None:
        """Declare that workspace `from_ws` depends on workspace `to_ws`."""
        self.add_node(from_ws)
        self.add_node(to_ws)
        self._deps[from_ws].add(to_ws)
        self._dependents[to_ws].add(from_ws)

    def rebuild(self) -> None:
        """Rebuild dependency graph from current registry state."""
        self._deps.clear()
        self._dependents.clear()
        self._nodes.clear()

        if not self._registry:
            return

        workspaces = self._registry.list_workspaces()
        for ws in workspaces:
            self.add_node(ws.name)
            for dep in ws.dependencies:
                # Add edge even if dep is not yet registered (external or sibling)
                self.add_dependency(ws.name, dep)

    def get_direct_dependencies(self, name: str) -> set[str]:
        """Return the immediate direct dependencies of a workspace."""
        return set(self._deps.get(name, set()))

    def get_dependencies(self, name: str, transitive: bool = True) -> set[str]:
        """Return all direct or transitive dependencies of a workspace."""
        if not transitive:
            return self.get_direct_dependencies(name)

        visited: set[str] = set()
        queue: deque[str] = deque(self.get_direct_dependencies(name))
        while queue:
            curr = queue.popleft()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self._deps.get(curr, set()) - visited)
        return visited

    def get_direct_dependents(self, name: str) -> set[str]:
        """Return workspaces that immediately depend on this workspace."""
        return set(self._dependents.get(name, set()))

    def get_dependents(self, name: str, transitive: bool = True) -> set[str]:
        """Return all direct or transitive downstream dependents of a workspace."""
        if not transitive:
            return self.get_direct_dependents(name)

        visited: set[str] = set()
        queue: deque[str] = deque(self.get_direct_dependents(name))
        while queue:
            curr = queue.popleft()
            if curr not in visited:
                visited.add(curr)
                queue.extend(self._dependents.get(curr, set()) - visited)
        return visited

    def get_impacted_workspaces(self, changed: list[str] | str) -> set[str]:
        """Identify all downstream workspaces affected by changes in given workspaces."""
        changed_list = [changed] if isinstance(changed, str) else list(changed)
        impacted: set[str] = set(changed_list)
        for name in changed_list:
            impacted.update(self.get_dependents(name, transitive=True))
        return impacted

    def detect_cycles(self) -> list[list[str]]:
        """Detect all simple cycles in the dependency graph using DFS."""
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)

            for neighbor in sorted(self._deps.get(node, set())):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in on_stack:
                    # Found cycle
                    idx = stack.index(neighbor)
                    cycle = [*stack[idx:], neighbor]
                    cycles.append(cycle)

            stack.pop()
            on_stack.remove(node)

        for n in sorted(self._nodes):
            if n not in visited:
                dfs(n)

        return cycles

    def topological_sort(self, subset: list[str] | None = None) -> list[str]:
        """Return workspace names in dependency execution order (leaves first).

        Raises DependencyCycleError if the graph contains cyclic dependencies.
        """
        cycles = self.detect_cycles()
        if cycles:
            raise DependencyCycleError(cycles[0])

        target_nodes = set(subset) if subset is not None else set(self._nodes)
        if subset is not None:
            # Expand to include transitive dependencies of subset items
            for item in subset:
                target_nodes.update(self.get_dependencies(item, transitive=True))

        # Compute in-degrees within target subgraph
        # Note: edge is from_ws -> to_ws (from_ws depends on to_ws).
        # Leaves (dependencies with 0 outbound edges in graph) must be executed first.
        # So we sort by in-degree of dependency requirements.
        in_degree: dict[str, int] = {
            n: len(self._deps.get(n, set()) & target_nodes) for n in target_nodes
        }

        queue: deque[str] = deque(sorted([n for n, deg in in_degree.items() if deg == 0]))
        sorted_order: list[str] = []

        while queue:
            curr = queue.popleft()
            sorted_order.append(curr)

            for dependent in sorted(self._dependents.get(curr, set()) & target_nodes):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(sorted_order) < len(target_nodes):
            unresolved = target_nodes - set(sorted_order)
            raise DependencyCycleError(list(unresolved))

        # If a subset was requested, filter to only the requested subset (preserving order)
        if subset is not None:
            subset_set = set(subset)
            return [n for n in sorted_order if n in subset_set]

        return sorted_order

    def render_ascii_tree(self) -> str:
        """Render a readable ASCII tree of workspaces and their dependencies."""
        if not self._nodes:
            return "(empty workspace mesh)"

        lines: list[str] = ["Monorepo Dependency Mesh:"]
        try:
            order = self.topological_sort()
            lines.append("  Topological Build Order: " + " -> ".join(order))
        except DependencyCycleError as exc:
            lines.append(f"  [!] Warning: {exc}")

        lines.append("\nWorkspaces & Dependencies:")
        for name in sorted(self._nodes):
            direct_deps = sorted(self.get_direct_dependencies(name))
            direct_downstream = sorted(self.get_direct_dependents(name))
            deps_str = ", ".join(direct_deps) if direct_deps else "none (leaf)"
            down_str = ", ".join(direct_downstream) if direct_downstream else "none (root)"
            lines.append(f"  * {name}")
            lines.append(f"      depends on: {deps_str}")
            lines.append(f"      used by:    {down_str}")

        return "\n".join(lines)

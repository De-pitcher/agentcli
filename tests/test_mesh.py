"""Comprehensive test suite for Phase 25: Multi-Repository Orchestration & Monorepo Mesh."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcli.config import Config, WorkspaceConfig
from agentcli.files import expand_file_references
from agentcli.mesh import (
    DependencyCycleError,
    MultiRepoIndex,
    ProjectDependencyGraph,
    WorkspaceRegistry,
    WorkspaceRoot,
    WorkspaceType,
)
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.workspace import WorkspaceAgent


def test_workspace_registry_registration_and_get():
    registry = WorkspaceRegistry()
    root = WorkspaceRoot(
        name="backend",
        path="/tmp/projects/backend",
        workspace_type=WorkspaceType.PYTHON,
        manifest_file="/tmp/projects/backend/pyproject.toml",
        dependencies=["shared"],
        tags=["api", "python"],
        description="Backend API Service",
    )
    registry.register(root)

    assert registry.get("backend") is not None
    assert registry.get("backend").name == "backend"
    assert registry.get("nonexistent") is None
    assert registry.names() == ["backend"]

    d = root.to_dict()
    assert d["name"] == "backend"
    assert d["workspace_type"] == "python"
    assert d["dependencies"] == ["shared"]


def test_workspace_registry_auto_discovery():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Sub-project 1: Python backend
        backend = root / "services" / "backend"
        backend.mkdir(parents=True)
        (backend / "pyproject.toml").write_text("[project]\nname = 'backend'\n", encoding="utf-8")

        # Sub-project 2: Node frontend
        frontend = root / "apps" / "frontend"
        frontend.mkdir(parents=True)
        (frontend / "package.json").write_text('{"name": "frontend", "dependencies": {"@scope/shared": "workspace:*"}}', encoding="utf-8")

        # Sub-project 3: Rust core
        core = root / "packages" / "core"
        core.mkdir(parents=True)
        (core / "Cargo.toml").write_text('[package]\nname = "core"\n', encoding="utf-8")

        registry = WorkspaceRegistry()
        discovered = registry.auto_discover(root_dir=root, max_depth=3)

        assert len(discovered) >= 3
        names = registry.names()
        assert "backend" in names
        assert "frontend" in names
        assert "core" in names

        frontend_ws = registry.get("frontend")
        assert frontend_ws is not None
        assert frontend_ws.workspace_type == WorkspaceType.NODE
        assert "shared" in frontend_ws.dependencies

        backend_ws = registry.get("backend")
        assert backend_ws is not None
        assert backend_ws.workspace_type == WorkspaceType.PYTHON


def test_workspace_registry_config_loading_and_path_resolution():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        api_dir = root / "api"
        api_dir.mkdir()
        api_file = api_dir / "routes.py"
        api_file.write_text("def get_users(): pass\n", encoding="utf-8")

        config_list = [
            WorkspaceConfig(
                name="api",
                path=str(api_dir),
                dependencies=["shared"],
                tags=["http"],
                description="REST API Gateway",
            )
        ]

        registry = WorkspaceRegistry()
        registry.load_from_config(config_list, base_dir=root)

        ws = registry.get("api")
        assert ws is not None
        assert ws.name == "api"
        assert ws.dependencies == ["shared"]

        # Path resolution with colon syntax
        ws_res, file_res = registry.resolve_path("api:routes.py")
        assert ws_res.name == "api"
        assert file_res == api_file.resolve()

        # Path resolution with slash syntax
        ws_res2, file_res2 = registry.resolve_path("api/routes.py")
        assert ws_res2.name == "api"
        assert file_res2 == api_file.resolve()

        # Non-existent workspace raises KeyError
        with pytest.raises(KeyError):
            registry.resolve_path("missing/file.py")


def test_dependency_graph_topological_sort_and_impact():
    registry = WorkspaceRegistry()
    registry.register(WorkspaceRoot(name="shared", path="/tmp/shared", dependencies=[]))
    registry.register(WorkspaceRoot(name="core", path="/tmp/core", dependencies=["shared"]))
    registry.register(WorkspaceRoot(name="backend", path="/tmp/backend", dependencies=["core"]))
    registry.register(WorkspaceRoot(name="frontend", path="/tmp/frontend", dependencies=["shared"]))
    registry.register(WorkspaceRoot(name="cli", path="/tmp/cli", dependencies=["backend", "frontend"]))

    graph = ProjectDependencyGraph(registry)

    # Topological sort (leaves first)
    order = graph.topological_sort()
    assert order.index("shared") < order.index("core")
    assert order.index("core") < order.index("backend")
    assert order.index("shared") < order.index("frontend")
    assert order.index("backend") < order.index("cli")
    assert order.index("frontend") < order.index("cli")

    # Direct & transitive dependencies
    assert graph.get_dependencies("cli", transitive=True) == {"backend", "frontend", "core", "shared"}
    assert graph.get_dependencies("cli", transitive=False) == {"backend", "frontend"}

    # Dependents & Downstream Impact analysis
    assert graph.get_dependents("shared", transitive=True) == {"core", "backend", "frontend", "cli"}
    impact = graph.get_impacted_workspaces("core")
    assert impact == {"core", "backend", "cli"}

    # Render ASCII tree check
    tree = graph.render_ascii_tree()
    assert "Monorepo Dependency Mesh" in tree
    assert "Topological Build Order" in tree
    assert "cli" in tree


def test_dependency_graph_cycle_detection():
    registry = WorkspaceRegistry()
    registry.register(WorkspaceRoot(name="service-a", path="/tmp/a", dependencies=["service-b"]))
    registry.register(WorkspaceRoot(name="service-b", path="/tmp/b", dependencies=["service-c"]))
    registry.register(WorkspaceRoot(name="service-c", path="/tmp/c", dependencies=["service-a"]))

    graph = ProjectDependencyGraph(registry)
    cycles = graph.detect_cycles()
    assert len(cycles) > 0

    with pytest.raises(DependencyCycleError) as exc_info:
        graph.topological_sort()
    assert "Circular workspace dependency detected" in str(exc_info.value)


@pytest.mark.asyncio
async def test_multi_repo_semantic_search():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Repo 1: Auth service
        auth_dir = root / "auth_service"
        auth_dir.mkdir()
        (auth_dir / "tokens.py").write_text(
            "def verify_jwt_token(token: str) -> bool:\n    # JWT signature verification\n    return True\n",
            encoding="utf-8",
        )

        # Repo 2: Billing service
        billing_dir = root / "billing_service"
        billing_dir.mkdir()
        (billing_dir / "invoices.py").write_text(
            "def calculate_vat_invoice(amount: float) -> float:\n    # Tax computation\n    return amount * 0.2\n",
            encoding="utf-8",
        )

        registry = WorkspaceRegistry()
        registry.register(WorkspaceRoot(name="auth", path=str(auth_dir)))
        registry.register(WorkspaceRoot(name="billing", path=str(billing_dir)))

        from agentcli.embeddings import VectorStore

        with VectorStore(db_path=str(root / "mesh_vectors.db")) as store:
            multi_index = MultiRepoIndex(registry=registry, store=store, similarity_threshold=0.01)

            # Index all workspaces
            counts = await multi_index.index_all()
            assert counts["auth"] >= 1
            assert counts["billing"] >= 1

            # Search across all workspaces
            results = await multi_index.search("JWT token verification")
            assert len(results) > 0
            assert results[0].workspace == "auth"
            assert "verify_jwt_token" in results[0].content
            formatted = results[0].format_block()
            assert "[auth]" in formatted

            # Scoped search in billing
            billing_results = await multi_index.search("tax invoice calculation", repo="billing")
            assert len(billing_results) > 0
            assert billing_results[0].workspace == "billing"
            assert "calculate_vat_invoice" in billing_results[0].content


def test_expand_repo_and_scoped_semantic_file_references():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs_dir = root / "docs_repo"
        docs_dir.mkdir()
        readme = docs_dir / "architecture.md"
        readme.write_text("# Monorepo Mesh Architecture\nExplaining sub-project topology.\n", encoding="utf-8")

        config = Config()
        config.mesh.workspaces = [WorkspaceConfig(name="docs", path=str(docs_dir))]
        config.mesh.auto_discover = False

        with patch("agentcli.config.load_config", return_value=config):
            prompt = "Please review @repo:docs/architecture.md and check design"
            expanded = expand_file_references(prompt)
            assert "Monorepo Mesh Architecture" in expanded
            assert "@repo:docs/architecture.md" not in expanded


@pytest.mark.asyncio
async def test_workspace_agent_monorepo_routing():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        client_dir = root / "client_app"
        client_dir.mkdir()
        (client_dir / "app.js").write_text("console.log('client start');\n", encoding="utf-8")

        config = Config()
        config.mesh.workspaces = [WorkspaceConfig(name="client", path=str(client_dir))]

        agent = WorkspaceAgent()
        with patch("agentcli.config.load_config", return_value=config):
            res = await agent.run(
                SubAgentTask(
                    agent_type=SubAgentType.WORKSPACE,
                    payload={
                        "operation": "search_files",
                        "target_workspace": "client",
                        "pattern": "*.js",
                    },
                )
            )

            assert res.success is True
            assert res.output["total_found"] >= 1
            assert any("app.js" in m for m in res.output["matches"])


@pytest.mark.asyncio
async def test_cli_mesh_subcommands(capsys):
    from agentcli.cli import run_mesh

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p_shared = root / "shared"
        p_shared.mkdir()
        p_api = root / "api"
        p_api.mkdir()

        config = Config()
        config.mesh.workspaces = [
            WorkspaceConfig(name="shared", path=str(p_shared), dependencies=[]),
            WorkspaceConfig(name="api", path=str(p_api), dependencies=["shared"]),
        ]
        config.mesh.auto_discover = False

        # 1. Test 'mesh list'
        args_list = argparse.Namespace(mesh_command="list", plain=True, no_color=True)
        code = await run_mesh(args_list, config)
        assert code == 0
        captured = capsys.readouterr().out
        assert "Monorepo Mesh Workspaces" in captured
        assert "shared" in captured
        assert "api" in captured

        # 2. Test 'mesh graph'
        args_graph = argparse.Namespace(mesh_command="graph", plain=True, no_color=True)
        code = await run_mesh(args_graph, config)
        assert code == 0
        captured = capsys.readouterr().out
        assert "Topological Build Order" in captured
        assert "shared -> api" in captured


@pytest.mark.asyncio
async def test_cli_mesh_search_and_run_subcommands(capsys):
    from agentcli.cli import run_mesh

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws_dir = root / "service_a"
        ws_dir.mkdir()
        (ws_dir / "calc.py").write_text("def multiply(x, y): return x * y\n", encoding="utf-8")

        config = Config()
        config.mesh.workspaces = [WorkspaceConfig(name="service_a", path=str(ws_dir))]
        config.mesh.auto_discover = False
        config.embeddings.cache_path = str(root / "mesh_cli.db")

        # 1. Test 'mesh search'
        args_search = argparse.Namespace(
            mesh_command="search",
            query="multiply",
            repo="service_a",
            top_k=2,
            threshold=0.01,
            index=True,
            plain=True,
            no_color=True,
        )
        code = await run_mesh(args_search, config)
        assert code == 0
        captured = capsys.readouterr().out
        assert "multiply" in captured or "match(es)" in captured

        # 2. Test 'mesh run'
        args_run = argparse.Namespace(
            mesh_command="run",
            task="Run check",
            repos="service_a",
            plain=True,
            no_color=True,
        )
        with patch("agentcli.session.AgentSession.run_loop") as mock_run:
            async def _empty_async_gen(*_args, **_kwargs):
                if False:
                    yield None
            mock_run.return_value = _empty_async_gen()
            code_run = await run_mesh(args_run, config)
            assert code_run == 0
            captured_run = capsys.readouterr().out
            assert "Executing task across monorepo mesh" in captured_run
            assert "Completed workspace: [service_a]" in captured_run


def test_dependency_graph_edge_cases():
    graph = ProjectDependencyGraph()
    assert graph.render_ascii_tree() == "(empty workspace mesh)"
    assert graph.detect_cycles() == []
    assert graph.topological_sort() == []

    graph.add_node("isolated")
    assert graph.topological_sort() == ["isolated"]
    assert graph.get_dependencies("isolated") == set()
    assert graph.get_dependents("isolated") == set()
    assert graph.get_impacted_workspaces("isolated") == {"isolated"}

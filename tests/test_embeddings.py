"""Comprehensive test suite for Phase 24 Semantic Vector Search and Embeddings."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcli.config import Config, OpenRouterConfig
from agentcli.embeddings import (
    CodeChunk,
    EmbeddingEngine,
    VectorIndex,
    VectorStore,
    chunk_file,
    chunk_markdown_file,
    chunk_python_file,
)
from agentcli.files import expand_file_references
from agentcli.subagents.base import SubAgentTask, SubAgentType
from agentcli.subagents.workspace import WorkspaceAgent


def test_chunk_python_file_ast():
    with tempfile.TemporaryDirectory() as td:
        py_file = Path(td) / "example.py"
        py_file.write_text(
            '"""Module docstring."""\n\n'
            "import os\n\n"
            "def calculate_tax(income: float) -> float:\n"
            "    rate = 0.2\n"
            "    return income * rate\n\n"
            "class InvoiceManager:\n"
            "    def __init__(self, currency: str):\n"
            "        self.currency = currency\n\n"
            "    def create(self) -> None:\n"
            "        pass\n",
            encoding="utf-8",
        )

        chunks = chunk_python_file(py_file, py_file.read_text(encoding="utf-8"))
        assert len(chunks) >= 2
        types = [c.chunk_type for c in chunks]
        assert "function" in types or "class" in types
        for chunk in chunks:
            assert chunk.file_path == str(py_file.resolve())
            assert chunk.start_line <= chunk.end_line
            assert len(chunk.sha256) == 64


def test_chunk_markdown_file_sections():
    with tempfile.TemporaryDirectory() as td:
        md_file = Path(td) / "README.md"
        md_file.write_text(
            "# Title\n\nIntroduction paragraph.\n\n"
            "## Installation\n\nRun pip install.\n\n"
            "## Usage\n\nRun agentcli chat.\n",
            encoding="utf-8",
        )

        chunks = chunk_markdown_file(md_file, md_file.read_text(encoding="utf-8"))
        assert len(chunks) == 3
        assert all(c.chunk_type == "markdown" for c in chunks)
        assert "Introduction paragraph" in chunks[0].content
        assert "Run pip install" in chunks[1].content


def test_chunk_generic_sliding_window():
    with tempfile.TemporaryDirectory() as td:
        js_file = Path(td) / "app.js"
        lines = [f"console.log('line {i}');" for i in range(100)]
        js_file.write_text("\n".join(lines), encoding="utf-8")

        chunks = chunk_file(js_file, max_lines=30, overlap_lines=5)
        assert len(chunks) >= 3
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 30
        assert chunks[1].start_line == 26


def test_vector_store_crud():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_vectors.db"
        with VectorStore(db_path=str(db_path)) as store:
            target_file = str((Path(td) / "file.py").resolve())
            chunk = CodeChunk(
                file_path=target_file,
                chunk_id="chunk_01",
                start_line=1,
                end_line=10,
                content="def hello(): pass",
                sha256="abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234",
                chunk_type="function",
            )
            vec = [0.1, 0.2, 0.3, 0.4]

            # Save single
            store.save_embedding(chunk, model="text-embedding-test", vector=vec)

            # Retrieve
            retrieved = store.get_embedding(chunk.chunk_id, chunk.sha256, model="text-embedding-test")
            assert retrieved is not None
            assert len(retrieved) == 4
            assert abs(retrieved[0] - 0.1) < 1e-4

            # Mismatched sha should return None (cache invalidation)
            assert store.get_embedding(chunk.chunk_id, "different_sha", model="text-embedding-test") is None

            # Batch save
            chunk2 = CodeChunk(
                file_path=target_file,
                chunk_id="chunk_02",
                start_line=11,
                end_line=20,
                content="def bye(): pass",
                sha256="efefefefefefefefefefefefefefefefefefefefefefefefefefefefefefefef",
                chunk_type="function",
            )
            store.save_embeddings_batch([(chunk2, "text-embedding-test", [0.5, 0.6, 0.7, 0.8])])

            all_records = store.get_all_for_model("text-embedding-test")
            assert len(all_records) == 2

            stats = store.stats()
            assert stats["total_chunks"] == 2
            assert stats["by_model"]["text-embedding-test"] == 2

            # Delete
            deleted = store.delete_file_chunks(target_file)
            assert deleted == 2
            assert store.stats()["total_chunks"] == 0


@pytest.mark.asyncio
async def test_embedding_engine_deterministic_fallback():
    engine = EmbeddingEngine(config=None, model="test-model")
    texts = ["def calculate_tax(): pass", "import os, sys"]
    vectors = await engine.embed_texts(texts)
    assert len(vectors) == 2
    assert len(vectors[0]) == 256
    assert len(vectors[1]) == 256

    query_vec = await engine.embed_query("tax calculation")
    assert len(query_vec) == 256


@pytest.mark.asyncio
async def test_embedding_engine_mock_api():
    config = OpenRouterConfig(api_key_env="DUMMY_KEY_ENV", base_url="https://api.openrouter.test/v1")
    with patch.dict(os.environ, {"DUMMY_KEY_ENV": "sk-test-key"}):
        engine = EmbeddingEngine(config=config, model="openai/text-embedding-3-small")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.3, 0.4, 0.0]},
                {"index": 1, "embedding": [0.0, 0.6, 0.8]},
            ]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            vectors = await engine.embed_texts(["hello", "world"])
            assert len(vectors) == 2
            assert len(vectors[0]) == 3
            # Check L2 normalization (0.3/0.5, 0.4/0.5) = (0.6, 0.8)
            assert abs(vectors[0][0] - 0.6) < 1e-4
            assert abs(vectors[0][1] - 0.8) < 1e-4


@pytest.mark.asyncio
async def test_vector_index_search_and_caching():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f_auth = root / "auth.py"
        f_auth.write_text(
            "def authenticate_user(token: str) -> bool:\n"
            "    # Verify JWT signature\n"
            "    return True\n",
            encoding="utf-8",
        )

        f_db = root / "db.py"
        f_db.write_text(
            "def connect_database(url: str):\n"
            "    # PostgreSQL connection pool\n"
            "    pass\n",
            encoding="utf-8",
        )

        db_path = root / "vectors.db"
        with VectorStore(db_path=str(db_path)) as store:
            engine = EmbeddingEngine()
            index = VectorIndex(store=store, engine=engine, similarity_threshold=0.01)

            # First indexing run: embeds 2 chunks
            indexed_count = await index.index_workspace(root=root)
            assert indexed_count >= 2

            # Second indexing run on unchanged files: should embed 0 chunks (cache hit)
            reindex_count = await index.index_workspace(root=root)
            assert reindex_count == 0

            # Search for authentication
            results = await index.search("JWT token verification", top_k=2)
            assert len(results) > 0
            assert "auth.py" in results[0].file_path
            assert "authenticate_user" in results[0].content
            formatted = results[0].format_block()
            assert "similarity:" in formatted


@pytest.mark.asyncio
async def test_workspace_agent_semantic_search():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "service.py").write_text(
            "class PaymentProcessor:\n"
            "    def charge_card(self, amount: float) -> str:\n"
            "        return 'tx_12345'\n",
            encoding="utf-8",
        )

        agent = WorkspaceAgent()
        res = await agent.run(
            SubAgentTask(
                agent_type=SubAgentType.WORKSPACE,
                payload={
                    "operation": "semantic_search",
                    "path": str(root),
                    "query": "credit card payment charge",
                    "top_k": 3,
                },
            )
        )

        assert res.success is True
        output = res.output
        assert output["query"] == "credit card payment charge"
        assert output["match_count"] >= 1
        assert "PaymentProcessor" in output["matches"][0]["content"]


def test_expand_semantic_references_token():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        py_file = root / "ledger.py"
        py_file.write_text("def record_ledger_entry(amount): pass\n", encoding="utf-8")

        # Manually seed vector store for deterministic matching
        from agentcli.embeddings import VectorIndex, VectorStore

        with VectorStore() as store:
            index = VectorIndex(store=store)
            chunks = chunk_file(py_file)
            vecs = [index.engine._deterministic_fallback_vector(c.content) for c in chunks]
            store.save_embeddings_batch([(c, index.engine.model, v) for c, v in zip(chunks, vecs, strict=False)])

            prompt = "Review this code @semantic:record_ledger_entry for audit"
            expanded = expand_file_references(prompt)
            assert "Review this code" in expanded
            assert "Semantic Search Context for: 'record ledger entry'" in expanded
            assert "record_ledger_entry" in expanded


@pytest.mark.asyncio
async def test_cli_search_subcommand(capsys):
    from agentcli.cli import run_search

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "math_ops.py").write_text(
            "def add_numbers(a, b):\n    return a + b\n",
            encoding="utf-8",
        )

        args = argparse.Namespace(
            query="add two numbers",
            top_k=2,
            threshold=0.01,
            filter=None,
            index=True,
            plain=True,
            no_color=True,
        )
        config = Config()
        config.embeddings.cache_path = str(root / "vectors.db")

        # Change cwd temporarily or pass root
        with patch("os.walk", return_value=[(str(root), [], ["math_ops.py"])]):
            exit_code = await run_search(args, config)
            assert exit_code == 0
            captured = capsys.readouterr().out
            assert "add_numbers" in captured or "Found" in captured


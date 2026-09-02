with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_subagents.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

old_test = '''    @pytest.mark.asyncio
    async def test_search_not_implemented(self) -> None:
        agent = WebSearchAgent()

        task = SubAgentTask(agent_type=SubAgentType.WEB_SEARCH, payload={"query": "python asyncio"})

        result = await agent.run(task)

        assert result.success is False

        assert "Web search is not yet implemented" in str(result.error)'''

new_test = '''    @pytest.mark.asyncio
    async def test_search_works(self) -> None:
        agent = WebSearchAgent()

        task = SubAgentTask(agent_type=SubAgentType.WEB_SEARCH, payload={
            "query": "python asyncio",
            "provider": "duckduckgo",
        })

        result = await agent.run(task)

        assert result.success is True
        assert result.output["count"] > 0
        assert len(result.output["results"]) > 0
        assert "provider" in result.output'''

if old_test in content:
    content = content.replace(old_test, new_test)
    with open('C:\\Users\\sam\\Documents\\sparkz\\agentcli\\tests\\test_subagents.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Test updated successfully')
else:
    print('Old test not found')
    idx = content.find('test_search_not_implemented')
    if idx >= 0:
        print(repr(content[idx:idx+300]))